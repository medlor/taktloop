from datetime import timedelta
from pathlib import Path

import pytest

from taktloop import orchestrator
from taktloop.cli_commands import CoreUnreachable, TokenRejected, ToolRejected
from taktloop.config import FactoryConfig, OrchestratorConfig
from taktloop.headless import StartFailed
from taktloop.models import ArtifactIn
from taktloop.orchestrator import CoreClient, Orchestrator
from taktloop.service import Caller, Rejected, Service
from taktloop.workflow import parse_workflow

ORCH = Caller("orchestrator", frozenset({"orchestrator", "verifier"}))
AGENT = Caller("agent-a", frozenset({"builder"}))
BOT = Caller("bot", frozenset({"reviewer"}))
REQ = Caller("tobias", frozenset({"requester", "approver", "factory_owner"}))
GH = {"owner": "medlor", "repo": "taktloop"}

WORKFLOW = {"start": "build", "steps": {
    "build": {"kind": "agent", "role": "builder", "skill": "s", "instructions": "i",
              "output": {"artifacts": ["change"]}, "next": "verify"},
    "verify": {
        "kind": "code", "role": "verifier",
        "call": {"mcp_server": "github", "tool": "actions_list",
                 "args": {"method": "list_workflow_runs", **GH, "resource_id": "ci.yml",
                          "workflow_runs_filter": {"branch": "{{artifacts.change.branch}}"}},
                 "evidence": {"ci_run": "workflow_runs.workflow_runs.0.id"}},
        "check": {"mcp_server": "github", "tool": "actions_get",
                  "args": {"method": "get_workflow_run", **GH, "resource_id": "{{evidence.ci_run}}"},
                  "match": {"workflow_run.head_sha": "{{artifacts.change.commit}}",
                            "workflow_run.status": "completed"},
                  "expect": {"workflow_run.conclusion": "success"}},
        "evidence": ["ci_run"], "next": "code_review", "on_fail": "build"},
    "code_review": {"kind": "agent", "role": "reviewer", "skill": "s", "instructions": "i",
                    "review_of": "build", "on_pass": "approval", "on_issues": "build",
                    "dispatch": "push", "push": {"start": "claude-headless", "identity": "bot"}},
    "approval": {"kind": "human_gate", "role": "approver", "on_approve": "done", "on_reject": "build"},
}}


class GitHub:
    """Fake `actions_list`/`actions_get`: runs by branch, newest first."""

    def __init__(self):
        self.runs: dict[str, list[dict]] = {}
        self.calls: list[tuple[str, dict]] = []

    def __call__(self, server, tool, args):
        if tool == "actions_get":
            run = next(r for rs in self.runs.values() for r in rs if str(r["id"]) == args["resource_id"])
            return {"workflow_run": dict(run)}
        self.calls.append((tool, args))
        runs = self.runs.get(args["workflow_runs_filter"]["branch"], [])
        return {"workflow_runs": {"total_count": len(runs), "workflow_runs": [dict(r) for r in runs]}}


class Proc:
    def __init__(self):
        self.code = None

    def poll(self):
        return self.code

    def kill(self):
        self.code = -9


class Launcher:
    def __init__(self):
        self.calls, self.procs, self.fail = [], [], None

    def __call__(self, step_id, token, shas):
        self.calls.append((step_id, token, shas))
        if self.fail:
            raise StartFailed(self.fail)
        self.procs.append(Proc())
        return self.procs[-1], Path(f".taktloop/runs/{step_id}-1.log")


@pytest.fixture
def env(store, clock):
    gh, launch, log = GitHub(), Launcher(), []
    svc = Service(store, parse_workflow(WORKFLOW), lease=timedelta(minutes=30), clock=clock, checker=gh)

    def core(tool, args):
        extra = {"outcome": "completed", "artifacts": []} if tool == "complete_step" else {}
        try:
            result = getattr(svc, tool)(ORCH, **extra, **args)
        except Rejected as e:
            raise ToolRejected(str(e)) from None
        if tool == "sweep":
            return {"steps": [s.model_dump(mode="json") for s in result]}
        return result.model_dump(mode="json")

    cfg = FactoryConfig("taktloop", "127.0.0.1", 8765, Path("db"), parse_workflow(WORKFLOW), 30,
                        "http://i", "taktloop", orchestrator=OrchestratorConfig("orchestrator"))

    class Env:
        pass

    e = Env()
    e.svc, e.gh, e.launch, e.log, e.clock, e.core, e.cfg = svc, gh, launch, log, clock, core, cfg
    e.new = lambda core=core: Orchestrator(cfg, core, gh, launch, lambda i: f"token-{i}", clock, log.append)
    e.orch = e.new()
    return e


def at_verify(e, n=1):
    sha = str(n) * 40
    t = e.svc.create_task(REQ, f"T{n}")
    c = e.svc.claim_step(AGENT, t.step_id)
    change = [ArtifactIn(name="change", path="src/x.py", commit=sha, branch=f"task-{n}")]
    return e.svc.complete_step(AGENT, t.step_id, c.claim_token, "completed", change), sha


def step(e, t):
    return e.svc.get_task(REQ, t.id).current_step


def stalls(e, t):
    return [ev.detail["reason"] for ev in e.svc.get_task(REQ, t.id).history if ev.operation == "report_stall"]


def test_code_step_refused_then_passes(env):
    t, sha = at_verify(env)
    env.gh.runs["task-1"] = [{"id": 101, "head_sha": sha, "status": "in_progress", "conclusion": None}]
    env.orch.tick()
    assert step(env, t) == "verify"
    assert env.svc.sweep(ORCH)[0].claimed_by == "orchestrator"
    env.clock.now += timedelta(seconds=30)
    env.orch.tick()
    assert len(env.gh.calls) == 1
    env.gh.runs["task-1"][0].update(status="completed", conclusion="success")
    env.clock.now += timedelta(seconds=31)
    env.orch.tick()
    assert step(env, t) == "code_review"
    assert env.gh.calls[0] == ("actions_list", {
        "method": "list_workflow_runs", "owner": "medlor", "repo": "taktloop",
        "resource_id": "ci.yml", "workflow_runs_filter": {"branch": "task-1"}})


def test_code_step_red_run_goes_to_on_fail(env):
    t, sha = at_verify(env)
    env.gh.runs["task-1"] = [{"id": 7, "head_sha": sha, "status": "completed", "conclusion": "failure"}]
    env.orch.tick()
    assert step(env, t) == "build"
    assert env.orch.claims == {}


def test_code_step_missing_run_waits(env):
    t, _ = at_verify(env)
    env.orch.tick()
    assert step(env, t) == "verify"
    assert "verify: no ci_run in the result" in env.log
    assert not [ev for ev in env.svc.get_task(REQ, t.id).history
                if ev.operation == "complete_step" and ev.subject == "orchestrator"]


def test_tick_two_code_steps_independent(env):
    t1, sha1 = at_verify(env, 1)
    t2, sha2 = at_verify(env, 2)
    env.gh.runs["task-1"] = [{"id": 1, "head_sha": sha1, "status": "in_progress", "conclusion": None}]
    env.gh.runs["task-2"] = [{"id": 2, "head_sha": sha2, "status": "in_progress", "conclusion": None}]
    env.orch.tick()
    assert len(env.orch.claims) == 2 and len(env.gh.calls) == 2
    env.gh.runs["task-1"][0].update(status="completed", conclusion="success")
    env.clock.now += timedelta(seconds=61)
    env.orch.tick()
    assert (step(env, t1), step(env, t2)) == ("code_review", "verify")
    assert list(env.orch.claims) == [t2.step_id]
    env.gh.runs["task-2"][0].update(status="completed", conclusion="success")
    env.clock.now += timedelta(seconds=30)  # task 2 was tried 30 s ago: wait
    env.orch.tick()
    assert step(env, t2) == "verify"
    env.clock.now += timedelta(seconds=30)
    env.orch.tick()
    assert step(env, t2) == "code_review"


def test_tick_skips_own_claim_without_token(env):
    t, sha = at_verify(env)
    env.gh.runs["task-1"] = [{"id": 5, "head_sha": sha, "status": "completed", "conclusion": "success"}]
    env.svc.claim_step(ORCH, t.step_id)  # a previous orchestrator process holds it
    env.orch.tick()
    assert env.gh.calls == [] and step(env, t) == "verify" and env.log == []
    env.clock.now += timedelta(minutes=31)  # lease runs out: the sweep expires it, the tick claims again
    env.orch.tick()
    assert step(env, t) == "code_review"


def test_tick_forgets_lost_claim(env):
    t, sha = at_verify(env)
    env.gh.runs["task-1"] = [{"id": 5, "head_sha": sha, "status": "in_progress", "conclusion": None}]
    env.orch.tick()
    old = env.orch.claims[t.step_id]

    def core(tool, args):  # the step is force-released after the sweep saw it claimed
        if tool == "heartbeat":
            env.svc.release_step(REQ, t.step_id, "stuck", force=True)
        return env.core(tool, args)

    env.orch.core = core
    env.clock.now += timedelta(seconds=61)
    env.orch.tick()
    assert env.orch.claims == {} and len(env.gh.calls) == 1
    assert any("not claimed" in line for line in env.log)
    env.orch.core = env.core
    env.orch.tick()  # open again: a fresh claim, never the dead token
    assert env.orch.claims[t.step_id] != old


def push_step(e, ticks=1):
    t, sha = at_verify(e)
    e.gh.runs["task-1"] = [{"id": 5, "head_sha": sha, "status": "completed", "conclusion": "success"}]
    e.orch.tick()  # verify passes; code_review opens after this tick's sweep
    assert step(e, t) == "code_review"
    for _ in range(ticks):
        e.orch.tick()
    return t, sha


def test_push_started_once(env):
    t, sha = push_step(env, ticks=3)
    sid = env.svc.get_task(REQ, t.id).step_id
    assert env.launch.calls == [(sid, "token-bot", [sha])]
    assert f"started code_review (step {sid}) as bot: .taktloop/runs/{sid}-1.log" in env.log


def test_push_child_killed_after_lease(env):
    t, _ = push_step(env)
    env.clock.now += timedelta(minutes=31)
    env.orch.tick()  # killed without having claimed: a failed start, then started again
    assert env.launch.procs[0].code == -9 and len(env.launch.calls) == 2
    assert env.orch.failed[env.svc.get_task(REQ, t.id).step_id] == 1


def test_push_children_exiting_unclaimed_stall(env):
    t, _ = push_step(env)
    for _ in range(3):
        env.launch.procs[-1].code = 1  # exits without claiming the step
        env.orch.tick()
    assert stalls(env, t) == ["lease expired or start failed 2 times"]
    assert len(env.launch.calls) == 2


def test_push_failed_starts_stall_and_stop(env):
    env.launch.fail = "no Claude login and no ANTHROPIC_API_KEY"
    t, _ = push_step(env, ticks=2)
    assert len(env.launch.calls) == 2 and stalls(env, t) == []
    env.orch.tick()  # flagged before a third start
    assert stalls(env, t) == ["lease expired or start failed 2 times"]
    assert len(env.launch.calls) == 2
    assert "code_review (step 3): StartFailed: no Claude login and no ANTHROPIC_API_KEY" in env.log


def test_stall_nobody_picked_up(env):
    t, sha = push_step(env)
    sid = env.svc.get_task(REQ, t.id).step_id
    c = env.svc.claim_step(BOT, sid)
    t = env.svc.complete_step(BOT, sid, c.claim_token, "pass", [])
    assert step(env, t) == "approval"
    env.clock.now += timedelta(minutes=61)
    env.orch.tick()
    env.orch.tick()
    assert stalls(env, t) == ["nobody picked it up for 60 min"]


def test_stall_code_step_not_through(env):
    t, sha = at_verify(env)
    env.gh.runs["task-1"] = [{"id": 5, "head_sha": sha, "status": "in_progress", "conclusion": None}]
    for _ in range(62):
        env.orch.tick()
        env.clock.now += timedelta(minutes=1)
    assert stalls(env, t) == ["code step not through after 60 min"]
    assert step(env, t) == "verify"


def test_tick_survives_core_down(env):
    def down(tool, args):
        raise CoreUnreachable("http://127.0.0.1:8765/mcp")

    env.new(down).tick()
    assert len(env.log) == 1 and "http://127.0.0.1:8765/mcp" in env.log[0]


def test_client_refreshes_token_on_401(monkeypatch, tmp_path):
    fetched, seen = [], []
    monkeypatch.setattr(orchestrator, "fetch_token", lambda i, c: fetched.append(i) or f"tok{len(fetched)}")

    async def call_tool(url, name, args, token, client):
        seen.append((token, client))
        if token == "tok1":
            raise TokenRejected
        return {"ok": name}

    monkeypatch.setattr(orchestrator, "_call_tool", call_tool)
    client = CoreClient("http://x/mcp", "orchestrator", tmp_path)
    assert client.call("sweep", {}) == {"ok": "sweep"}
    assert fetched == ["orchestrator", "orchestrator"]
    assert seen == [("tok1", "taktloop-orchestrator"), ("tok2", "taktloop-orchestrator")]
    client.call("sweep", {})
    assert len(fetched) == 2

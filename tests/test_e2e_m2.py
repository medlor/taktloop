"""The M2 demo (spec §9, steps 2-8) against a real dev issuer and a real core, both subprocesses,
with `separation: strict` and the shipped workflow: tokens per identity, plan review, escalation, override."""

import json
import os
import signal
import socket
import subprocess
import time
from pathlib import Path

import anyio
import httpx2
import pytest
from mcp import Client, Implementation
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from taktloop.cli import main
from taktloop.token_client import fetch_token

ROOT = Path(__file__).resolve().parent.parent
pytestmark = pytest.mark.e2e

ISSUER = """\
issuer: http://127.0.0.1:{port}
audience: taktloop
token_lifetime: 12h
state_dir: issuer-state
identities:
  tobias:  {{scopes: [taktloop:taktloop:requester, taktloop:taktloop:approver,
                     taktloop:taktloop:reviewer, taktloop:taktloop:factory_owner]}}
  agent-a: {{scopes: [taktloop:taktloop:planner, taktloop:taktloop:reviewer]}}
  agent-b: {{scopes: [taktloop:taktloop:planner, taktloop:taktloop:reviewer]}}
"""

CORE_HEADER = """\
factory: taktloop
server: {{host: 127.0.0.1, port: {port}}}
database: {db}
auth: {{issuer: {issuer}, audience: taktloop}}
cli: {{identity: tobias}}
separation: strict
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _start(args: list[str], log: Path, ready) -> subprocess.Popen:
    env = {k: v for k, v in os.environ.items() if k != "TAKTLOOP_TOKEN"}  # servers hold no token
    with open(log, "w") as out:
        proc = subprocess.Popen(["uv", "run", "taktloop", *args], cwd=ROOT, stdout=out, env=env,
                                stderr=subprocess.STDOUT, start_new_session=True)
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if proc.poll() is None and ready():
            return proc
        time.sleep(0.2)
    _stop(proc)
    pytest.fail(f"{args} did not become ready:\n{log.read_text()}")


def _stop(proc: subprocess.Popen) -> None:
    if proc.poll() is None:
        os.killpg(proc.pid, signal.SIGTERM)  # uv and the process it started
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            os.killpg(proc.pid, signal.SIGKILL)
            proc.wait()


def _answers(url: str) -> bool:
    try:
        return httpx2.get(url, timeout=1).status_code == 200
    except httpx2.HTTPError:
        return False


@pytest.fixture
def factory(tmp_path, monkeypatch, request):
    """Issuer and core subprocesses; yields (core URL, credentials dir)."""
    issuer = f"http://127.0.0.1:{_free_port()}"
    issuer_cfg = tmp_path / "dev-issuer.yaml"
    issuer_cfg.write_text(ISSUER.format(port=issuer.rsplit(":", 1)[1]))
    creds = tmp_path / "credentials"
    init = subprocess.run(["uv", "run", "taktloop", "issuer", "init", "--config", str(issuer_cfg),
                           "--credentials", str(creds)], cwd=ROOT, capture_output=True, text=True)
    assert init.returncode == 0, init.stderr
    assert sorted(p.name for p in creds.iterdir()) == ["agent-a.json", "agent-b.json", "tobias.json"]
    issuer_proc = _start(["issuer", "--config", str(issuer_cfg)], tmp_path / "issuer.log",
                         lambda: _answers(f"{issuer}/jwks.json"))
    request.addfinalizer(lambda: _stop(issuer_proc))  # also when the core fails to start

    port = _free_port()
    workflow = (ROOT / "templates/mvp/factory.yaml").read_text()
    workflow = workflow[workflow.index("workflow:"):]  # the shipped workflow, with plan_review
    core_cfg = tmp_path / "factory.yaml"
    core_cfg.write_text(CORE_HEADER.format(port=port, db=tmp_path / "f.db", issuer=issuer) + workflow)
    url = f"http://127.0.0.1:{port}/mcp"
    monkeypatch.setenv("TAKTLOOP_TOKEN", fetch_token("tobias", creds))  # the CLI runs as tobias
    core_proc = _start(["--config", str(core_cfg), "serve"], tmp_path / "core.log",
                       lambda: main(["--url", url, "task", "list"]) == 0)
    yield url, creds
    _stop(core_proc)


class Session:
    """An agent session in Claude Code, started as `TAKTLOOP_TOKEN=$(taktloop token <id>) claude`."""

    def __init__(self, url: str, token: str):
        self.url, self.token = url, token

    async def _call(self, tool: str, args: dict):
        http = create_mcp_http_client(headers={"Authorization": f"Bearer {self.token}"})
        client = Client(streamable_http_client(self.url, http_client=http),
                        client_info=Implementation(name="claude-code", version="0"))
        async with client as c:
            return await c.call_tool(tool, args)

    def call(self, tool: str, **args):
        r = anyio.run(self._call, tool, args)
        assert not r.is_error, r.content[0].text
        return r.structured_content

    def refused(self, tool: str, **args) -> str:
        r = anyio.run(self._call, tool, args)
        assert r.is_error, r
        return r.content[0].text

    def open_step(self, step: str) -> int:
        steps = [s for s in self.call("list_open_steps")["steps"] if s["step"] == step]
        assert len(steps) == 1, steps
        return steps[0]["step_id"]

    def work(self, step: str, artifact: str, commit: str) -> dict:
        step_id = self.open_step(step)
        claim = self.call("claim_step", step_id=step_id)["claim_token"]
        art = {"name": artifact, "path": f"docs/{artifact}.md", "commit": commit, "branch": "beta"}
        return self.call("complete_step", step_id=step_id, claim_token=claim, artifacts=[art])

    def review(self, findings: str) -> dict:
        step_id = self.open_step("plan_review")
        claim = self.call("claim_step", step_id=step_id)["claim_token"]
        ctx = self.call("get_step_context", step_id=step_id)
        assert ctx["review_of"] == "plan" and ctx["skill"] == "taktloop-plan-review"
        return self.call("complete_step", step_id=step_id, claim_token=claim, outcome="issues",
                         findings=findings)


def test_m2_demo(factory, capsys):
    url, creds = factory
    cli = lambda *a: main(["--url", url, *a])  # noqa: E731
    agent_a = Session(url, fetch_token("agent-a", creds))
    agent_b = Session(url, fetch_token("agent-b", creds))

    # 2. tobias creates the task
    assert cli("task", "create", "Add a --version flag") == 0
    tid = 1

    # 3. agent-a runs design, tobias approves; a call without a token gets 401
    agent_a.work("design", "design_doc", "a" * 40)
    assert cli("approve", str(tid)) == 0
    r = httpx2.post(url, json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                    headers={"Accept": "application/json, text/event-stream"})
    assert r.status_code == 401

    # 4. agent-a writes the plan
    assert agent_a.work("plan", "plan", "b" * 40)["current_step"] == "plan_review"

    # 5. agent-a may not review its own plan; agent-b returns issues
    review_id = agent_a.open_step("plan_review")
    reason = agent_a.refused("claim_step", step_id=review_id)
    assert reason.endswith(f"strict separation: agent-a completed plan round 1 of task {tid}"), reason
    assert agent_b.review("1. Task 3 has no test for the flag's output.")["current_step"] == "plan"

    # 6. agent-a revises with the findings as feedback; the 2nd issues verdict escalates
    plan_id = agent_a.open_step("plan")
    ctx = agent_a.call("get_step_context", step_id=plan_id)
    assert ctx["round"] == 2
    assert (ctx["feedback"]["kind"], ctx["feedback"]["by"]) == ("review_issues", "agent-b")
    assert ctx["feedback"]["text"] == "1. Task 3 has no test for the flag's output."
    agent_a.work("plan", "plan", "c" * 40)
    escalated = agent_b.review("1. Still no test for the output.")
    assert escalated["current_step"] == "plan_escalation"
    assert escalated["waiting_for"] == (
        "waiting for approver decision at plan_escalation (plan failed review 2 times)")

    # 7. tobias overrides the review
    assert cli("override", str(tid), "--reason", "Good enough; the test is added during build") == 0

    # 8. the history names identity, roles and executor for every event
    capsys.readouterr()
    assert cli("--json", "task", "show", str(tid)) == 0
    task = json.loads(capsys.readouterr().out)
    assert task["status"] == "done"
    assert [a["commit"][0] for a in task["artifacts"] if a["name"] == "plan"][-1] == "c"
    history = task["history"]
    for e in history:  # every identity's call carries its roles; the core's own events have none
        assert e["subject"] and (e["roles"] or (e["subject"], e["operation"]) == ("core", "escalate")), e
    assert history[0]["operation"] == "create_task" and history[0]["subject"] == "tobias"

    agent_roles = ["planner", "reviewer"]
    tobias_roles = ["approver", "factory_owner", "requester", "reviewer"]
    refused = [e for e in history if e["result"] == "rejected"]
    assert [(e["operation"], e["step"], e["subject"], e["roles"], e["executor"]) for e in refused] == [
        ("claim_step", "plan_review", "agent-a", agent_roles, "claude-code")]
    assert "strict separation" in refused[0]["reason"]

    verdicts = [e for e in history if e["operation"] == "complete_step" and e["step"] == "plan_review"]
    assert [(e["subject"], e["roles"], e["executor"], e["detail"]["outcome"]) for e in verdicts] == [
        ("agent-b", agent_roles, "claude-code", "issues")] * 2
    assert [e["detail"]["next"] for e in verdicts] == ["plan", "plan_escalation"]
    (esc,) = [e for e in history if e["operation"] == "escalate"]
    assert history.index(esc) == history.index(verdicts[1]) + 1
    assert (esc["subject"], esc["step"], esc["detail"]["issue_rounds"]) == ("core", "plan_escalation", 2)

    (override,) = [e for e in history if e["operation"] == "override_review"]
    assert (override["subject"], override["roles"], override["executor"], override["step"],
            override["result"]) == ("tobias", tobias_roles, "taktloop-cli", "plan_escalation", "ok")
    assert override["detail"]["reason"] == "Good enough; the test is added during build"
    assert override["detail"]["verdicts"] == [
        {"outcome": "issues", "by": "agent-b", "round": 1},
        {"outcome": "issues", "by": "agent-b", "round": 2}]
    assert [a["commit"] for a in override["detail"]["approved"]] == ["c" * 40]

    capsys.readouterr()
    assert cli("task", "show", str(tid)) == 0
    shown = capsys.readouterr().out
    assert "agent-b (claude-code)" in shown and "tobias (taktloop-cli)" in shown
    assert "override: Good enough" in shown
    assert "issues → plan" in shown and "issues → plan_escalation" in shown
    assert "plan_review failed 2 times → plan_escalation" in shown

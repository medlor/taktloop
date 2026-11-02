from datetime import timedelta

import pytest
import yaml

from taktloop.checks import CheckUnavailable
from taktloop.models import ArtifactIn
from taktloop.service import Caller, Rejected, Service
from taktloop.workflow import parse_workflow

WF = parse_workflow(yaml.safe_load("""
start: build
steps:
  build:
    kind: agent
    role: builder
    skill: subagent-driven-development
    output: {artifacts: [change]}
    next: verify
  verify:
    kind: agent
    role: verifier
    skill: verification-before-completion
    evidence: [ci_run]
    check:
      mcp_server: github
      tool: actions_get
      args: {method: get_workflow_run, owner: medlor, repo: taktloop,
             resource_id: "{{evidence.ci_run}}"}
      match:
        workflow_run.head_sha: "{{artifacts.change.commit}}"
        workflow_run.path: .github/workflows/ci.yml
        workflow_run.status: completed
      expect:
        workflow_run.conclusion: success
    next: done
    on_fail: build
"""))
BOT = Caller("agent-a", frozenset({"requester", "builder", "verifier"}), "claude-code")
OTHER = Caller("agent-b", frozenset({"verifier"}), "codex")
OWNER = Caller("tobias", frozenset({"factory_owner"}))
SHA_A, SHA_B = "a" * 40, "b" * 40


def ci(sha=SHA_A, status="completed", conclusion="success"):
    return {"method": "get_workflow_run", "workflow_run": {
        "head_sha": sha, "path": ".github/workflows/ci.yml", "status": status,
        "conclusion": conclusion}}


@pytest.fixture
def env(store, clock):
    results, calls = [], []

    def checker(server, tool, args):
        calls.append((server, tool, args))
        r = results.pop(0)
        if isinstance(r, Exception):
            raise r
        return r() if callable(r) else r
    return Service(store, WF, clock=clock, checker=checker), results, calls


def build(svc, step_id, sha=SHA_A):
    c = svc.claim_step(BOT, step_id)
    art = ArtifactIn(name="change", path=".", commit=sha, branch="taktloop/1-x")
    return svc.complete_step(BOT, step_id, c.claim_token, "completed", [art])


def at_verify(svc, sha=SHA_A):
    """A task built at `sha`, its verify step claimed: (verify step id, complete(evidence))."""
    t = build(svc, svc.create_task(BOT, "T").step_id, sha)
    c = svc.claim_step(BOT, t.step_id)
    return t.step_id, lambda ev={"ci_run": "12345"}: svc.complete_step(
        BOT, t.step_id, c.claim_token, "completed", [], evidence=ev)


def history(svc, op):
    return [e for e in svc.get_task(BOT, 1).history if e.operation == op]


def test_check_pass_advances(env):
    svc, results, calls = env
    _, complete = at_verify(svc)
    results.append(ci())
    assert complete().status == "done"
    [check] = history(svc, "check")
    assert check.subject == "core" and check.detail["result"] == "pass"
    run = ci()["workflow_run"]  # observed: what the result returned for each declared field
    assert check.detail["observed"] == {f"workflow_run.{k}": v for k, v in run.items()}
    assert calls[0][:2] == ("github", "actions_get") and calls[0][2]["resource_id"] == "12345"
    assert history(svc, "complete_step")[-1].detail["evidence"] == {"ci_run": "12345"}


def test_check_match_mismatch_refuses_and_keeps_claim(env):
    svc, results, _ = env
    sid, complete = at_verify(svc)
    results.append(ci(status="in_progress", conclusion=None))
    with pytest.raises(Rejected, match="workflow_run.status is in_progress, expected completed"):
        complete()
    t = svc.get_task(BOT, 1)
    assert t.current_step == "verify" and "in progress by agent-a" in t.waiting_for
    [check] = history(svc, "check")
    assert check.detail["result"] == "mismatch"
    assert check.detail["observed"]["workflow_run.conclusion"] is None
    results.append(ci())
    assert complete().status == "done"


def test_check_expect_failure_reopens_build_with_feedback(env):
    svc, results, _ = env
    _, complete = at_verify(svc)
    results.append(ci(conclusion="failure"))
    t = complete()
    assert t.current_step == "build"
    ctx = svc.get_step_context(BOT, t.step_id)
    assert ctx.round == 2
    assert ctx.feedback.kind == "check_failed" and ctx.feedback.by == "core"
    assert "ci_run=12345" in ctx.feedback.text
    assert "workflow_run.conclusion is failure, expected success" in ctx.feedback.text
    done = history(svc, "complete_step")[-1].detail
    assert (done["outcome"], done["next"]) == ("failed", "build")


def test_check_uses_latest_change_round(env):
    svc, results, _ = env
    _, complete = at_verify(svc, SHA_A)
    results.append(ci(SHA_A, conclusion="failure"))
    t = build(svc, complete().step_id, SHA_B)
    c = svc.claim_step(BOT, t.step_id)
    results.append(ci(SHA_A))
    with pytest.raises(Rejected, match="head_sha is " + SHA_A):
        svc.complete_step(BOT, t.step_id, c.claim_token, "completed", [],
                          evidence={"ci_run": "111"})
    results.append(ci(SHA_B))
    assert svc.complete_step(BOT, t.step_id, c.claim_token, "completed", [],
                             evidence={"ci_run": "222"}).status == "done"


def test_check_unavailable_changes_nothing(env):
    svc, results, _ = env
    _, complete = at_verify(svc)
    results.append(CheckUnavailable("github is unreachable: ConnectError"))
    with pytest.raises(Rejected) as e:
        complete()
    assert e.value.reason == "CI check could not run: github is unreachable: ConnectError"
    assert "in progress by agent-a" in svc.get_task(BOT, 1).waiting_for
    assert history(svc, "check") == []
    assert [x.result for x in history(svc, "complete_step")] == ["ok", "rejected"]  # build, verify
    results.append(ci())
    assert complete().status == "done"


def test_check_without_checker_is_refused(store, clock):
    svc = Service(store, WF, clock=clock)
    _, complete = at_verify(svc)
    with pytest.raises(Rejected, match="^CI check could not run: no checker configured$"):
        complete()


@pytest.mark.parametrize("lose, refusal, waiting", [
    ("force_release", "not claimed", "waiting for a verifier to pick up verify"),
    ("lease_expired", "not claimed", "waiting for a verifier to pick up verify"),
    ("claimed_again", "^step is claimed by agent-b, not you$", "verify in progress by agent-b")],
    ids=["force_release", "lease_expired", "claimed_again"])
def test_check_claim_lost_during_call(env, clock, lose, refusal, waiting):
    svc, results, _ = env
    sid, complete = at_verify(svc)

    def lost():  # service calls during the check call: no transaction is open
        if lose == "force_release":
            svc.release_step(OWNER, sid, "stuck", force=True)
        else:
            clock.now += timedelta(minutes=31)
        if lose == "claimed_again":
            svc.claim_step(OTHER, sid)
        return ci()
    results.append(lost)
    with pytest.raises(Rejected, match=refusal):
        complete()
    t = svc.get_task(BOT, 1)
    assert t.current_step == "verify" and t.status == "active"
    assert t.waiting_for.startswith(waiting)


def test_evidence_rules(env):
    svc, _, calls = env
    _, complete = at_verify(svc)
    for ev in (None, {}, {"ci_run": "1", "extra": "2"}, {"ci_run": " "}):
        with pytest.raises(Rejected, match="^evidence must be exactly: ci_run$"):
            complete(ev)
    assert calls == []
    t = svc.create_task(BOT, "U")
    c = svc.claim_step(BOT, t.step_id)
    art = ArtifactIn(name="change", path=".", commit=SHA_A, branch="b")
    with pytest.raises(Rejected, match="^this step takes no evidence$"):
        svc.complete_step(BOT, t.step_id, c.claim_token, "completed", [art],
                          evidence={"ci_run": "1"})

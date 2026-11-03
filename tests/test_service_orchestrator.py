from datetime import timedelta

import pytest

from conftest import SHA

from taktloop.models import ArtifactIn
from taktloop.service import Caller, Rejected, Service
from taktloop.workflow import parse_workflow

ORCH = Caller("orchestrator", frozenset({"orchestrator", "verifier"}))
AGENT = Caller("agent-a", frozenset({"builder", "reviewer"}))
REQ = Caller("tobias", frozenset({"requester"}))
CHANGE = [ArtifactIn(name="change", path="src/x.py", commit=SHA, branch="task-1")]

WORKFLOW = {"start": "build", "steps": {
    "build": {"kind": "agent", "role": "builder", "skill": "s", "instructions": "i",
              "output": {"artifacts": ["change"]}, "next": "verify"},
    "verify": {
        "kind": "code", "role": "verifier",
        "call": {"mcp_server": "github", "tool": "actions_list",
                 "args": {"workflow_runs_filter": {"branch": "{{artifacts.change.branch}}"}},
                 "evidence": {"ci_run": "workflow_runs.workflow_runs.0.id"}},
        "check": {"mcp_server": "github", "tool": "actions_get",
                  "args": {"resource_id": "{{evidence.ci_run}}"},
                  "match": {"workflow_run.head_sha": "{{artifacts.change.commit}}"},
                  "expect": {"workflow_run.conclusion": "success"}},
        "evidence": ["ci_run"], "next": "code_review", "on_fail": "build"},
    "code_review": {"kind": "agent", "role": "reviewer", "skill": "s", "instructions": "i",
                    "review_of": "build", "on_pass": "done", "on_issues": "build",
                    "dispatch": "push", "push": {"start": "claude-headless", "identity": "bot"}},
}}


@pytest.fixture
def svc_m3b(store, clock):
    return Service(store, parse_workflow(WORKFLOW), lease=timedelta(minutes=30), clock=clock)


def at_verify(svc):
    t = svc.create_task(REQ, "T")
    c = svc.claim_step(AGENT, t.step_id)
    return svc.complete_step(AGENT, t.step_id, c.claim_token, "completed", CHANGE)


def events(svc, task_id, op):
    return [e for e in svc.get_task(REQ, task_id).history if e.operation == op]


def test_sweep_lists_open_and_claimed(svc_m3b, clock):
    t = svc_m3b.create_task(REQ, "T")
    (s,) = svc_m3b.sweep(ORCH)
    assert (s.step_id, s.task_id, s.step, s.kind, s.role, s.dispatch, s.push_identity) == (
        t.step_id, t.id, "build", "agent", "builder", "pull", None)
    assert (s.status, s.claimed_by, s.lease_expiries, s.stalled, s.idle_since) == (
        "open", None, 0, False, clock.now)
    svc_m3b.claim_step(AGENT, t.step_id)
    assert [(x.status, x.claimed_by) for x in svc_m3b.sweep(ORCH)] == [("claimed", "agent-a")]


def test_sweep_expires_and_counts(svc_m3b, clock):
    t = svc_m3b.create_task(REQ, "T")
    svc_m3b.claim_step(AGENT, t.step_id)
    clock.now += timedelta(minutes=31)
    s = svc_m3b.sweep(ORCH)[0]
    assert (s.status, s.lease_expiries, s.idle_since) == ("open", 1, clock.now)
    assert [e.subject for e in events(svc_m3b, t.id, "lease_expired")] == ["core"]


def test_sweep_requires_orchestrator_role(svc_m3b):
    with pytest.raises(Rejected, match="sweep requires role orchestrator"):
        svc_m3b.sweep(AGENT)


def test_report_stall_once(svc_m3b):
    t = svc_m3b.create_task(REQ, "T")
    s = svc_m3b.report_stall(ORCH, t.step_id, "nobody picked it up for 60 min")
    assert s.waiting_for.endswith(" — stalled: nobody picked it up for 60 min")
    svc_m3b.report_stall(ORCH, t.step_id, "again")
    assert [(e.subject, e.detail) for e in events(svc_m3b, t.id, "report_stall")] == [
        ("orchestrator", {"step_id": t.step_id, "reason": "nobody picked it up for 60 min"})]
    assert svc_m3b.sweep(ORCH)[0].stalled is True


def test_report_stall_refusals(svc_m3b):
    t = svc_m3b.create_task(REQ, "T")
    with pytest.raises(Rejected, match="reason is required"):
        svc_m3b.report_stall(ORCH, t.step_id, "  ")
    with pytest.raises(Rejected, match="report_stall requires role orchestrator"):
        svc_m3b.report_stall(AGENT, t.step_id, "x")
    c = svc_m3b.claim_step(AGENT, t.step_id)
    svc_m3b.complete_step(AGENT, t.step_id, c.claim_token, "completed", CHANGE)
    with pytest.raises(Rejected, match=r"step is not open \(status: completed\)"):
        svc_m3b.report_stall(ORCH, t.step_id, "x")


def test_code_step_claimable_and_listed(svc_m3b):
    t = at_verify(svc_m3b)
    assert t.waiting_for == "waiting for the orchestrator to run verify"
    (o,) = svc_m3b.list_open_steps(ORCH)
    assert (o.step, o.skill) == ("verify", "")
    assert svc_m3b.list_open_steps(AGENT) == []
    assert svc_m3b.claim_step(ORCH, t.step_id).step_id == t.step_id

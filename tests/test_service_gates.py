from datetime import timedelta

import pytest

from taktloop.models import ArtifactIn
from taktloop.service import Rejected
from test_service_steps import ME, SHA, clock, store, svc  # noqa: F401

SHA2 = "b" * 40


def complete_design(svc, task_id, sha=SHA):
    step_id = svc.get_task(ME, task_id).step_id
    c = svc.claim_step(ME, step_id)
    return svc.complete_step(ME, step_id, c.claim_token, "completed",
                             [ArtifactIn(name="design_doc", path="docs/d.md", commit=sha,
                                         branch="main")])


def at_gate(svc):
    t = svc.create_task(ME, "T", "do it")
    complete_design(svc, t.id)
    return t.id


def test_approve_opens_plan_and_context_has_design_doc(svc):
    tid = at_gate(svc)
    svc.decide_gate(ME, tid, None, "approve")
    [o] = svc.list_open_steps(ME)
    assert o.step == "plan"
    ctx = svc.get_step_context(ME, o.step_id)
    assert ctx.artifacts[0].name == "design_doc" and ctx.artifacts[0].commit == SHA


def test_reject_reopens_design_round_2_with_reason(svc):
    tid = at_gate(svc)
    svc.decide_gate(ME, tid, "design_approval", "reject", "needs CLI flags")
    [o] = svc.list_open_steps(ME)
    assert o.step == "design" and o.round == 2
    ctx = svc.get_step_context(ME, o.step_id)
    assert ctx.rejection_reason == "needs CLI flags"
    assert ctx.artifacts == []


def test_reject_requires_reason(svc):
    tid = at_gate(svc)
    for r in (None, "  "):
        with pytest.raises(Rejected, match="reason"):
            svc.decide_gate(ME, tid, None, "reject", r)
    assert svc.get_task(ME, tid).current_step == "design_approval"


def test_gate_rejected_when_not_at_gate(svc):
    t = svc.create_task(ME, "T")
    with pytest.raises(Rejected, match="not at a gate"):
        svc.decide_gate(ME, t.id, None, "approve")
    complete_design(svc, t.id)
    svc.decide_gate(ME, t.id, None, "approve")
    c = svc.claim_step(ME, svc.get_task(ME, t.id).step_id)
    svc.complete_step(ME, c.step_id, c.claim_token, "completed",
                      [ArtifactIn(name="plan", path="p.md", commit=SHA, branch="main")])
    with pytest.raises(Rejected, match="not at a gate"):
        svc.decide_gate(ME, t.id, None, "approve")
    with pytest.raises(Rejected, match="unknown task 99"):
        svc.decide_gate(ME, 99, None, "approve")
    rej = [e for e in svc.get_task(ME, t.id).history if e.result == "rejected"]
    assert len(rej) == 2 and all("not at a gate" in e.reason for e in rej)


def test_named_gate_mismatch(svc):
    tid = at_gate(svc)
    with pytest.raises(Rejected, match="design_approval.*other"):
        svc.decide_gate(ME, tid, "other", "approve")
    with pytest.raises(Rejected, match="decision"):
        svc.decide_gate(ME, tid, None, "maybe")


def test_full_flow_reaches_done(svc):
    tid = at_gate(svc)
    svc.decide_gate(ME, tid, None, "approve")
    c = svc.claim_step(ME, svc.get_task(ME, tid).step_id)
    s = svc.complete_step(ME, c.step_id, c.claim_token, "completed",
                          [ArtifactIn(name="plan", path="plan.md", commit=SHA2, branch="main")])
    assert s.status == "done" and s.waiting_for == "done"
    d = svc.get_task(ME, tid)
    assert d.status == "done" and len(d.artifacts) == 2 and d.request == "do it"
    assert [(a.step, a.round) for a in d.artifacts] == [("design", 1), ("plan", 1)]
    assert [e.operation for e in d.history if e.result == "ok"] == [
        "create_task", "claim_step", "complete_step", "decide_gate", "claim_step",
        "complete_step"]
    assert d.history[0].step == "design"


def test_approve_event_pins_artifacts(svc):
    tid = at_gate(svc)
    svc.decide_gate(ME, tid, None, "reject", "again")
    complete_design(svc, tid, SHA2)
    svc.decide_gate(ME, tid, None, "approve")
    d = svc.get_task(ME, tid)
    assert len(d.artifacts) == 2  # full history, both rounds
    gates = [e for e in d.history if e.operation == "decide_gate" and e.result == "ok"]
    assert gates[0].detail["artifacts"][0]["commit"] == SHA
    assert gates[0].detail["gate"] == "design_approval"
    assert gates[1].detail["artifacts"] == [
        {"name": "design_doc", "path": "docs/d.md", "commit": SHA2, "branch": "main",
         "step": "design", "round": 2}]
    [o] = svc.list_open_steps(ME)
    assert svc.get_step_context(ME, o.step_id).artifacts[0].commit == SHA2


def test_list_tasks_hides_done_by_default(svc):
    a = svc.create_task(ME, "A")
    b = svc.create_task(ME, "B")
    c = svc.claim_step(ME, b.step_id)
    svc.complete_step(ME, b.step_id, c.claim_token, "completed",
                      [ArtifactIn(name="design_doc", path="d.md", commit=SHA, branch="m")])
    svc.decide_gate(ME, b.id, None, "approve")
    c = svc.claim_step(ME, svc.get_task(ME, b.id).step_id)
    svc.complete_step(ME, c.step_id, c.claim_token, "completed",
                      [ArtifactIn(name="plan", path="p.md", commit=SHA, branch="m")])
    assert [t.id for t in svc.list_tasks(ME)] == [a.id]
    assert [t.id for t in svc.list_tasks(ME, include_closed=True)] == [a.id, b.id]


def test_get_task_unknown_rejected(svc):
    with pytest.raises(Rejected, match="unknown task 5"):
        svc.get_task(ME, 5)


def test_old_token_rejected_after_expiry_and_reclaim(svc, clock):
    t = svc.create_task(ME, "T")
    first = svc.claim_step(ME, t.step_id)
    clock.now += timedelta(minutes=31)
    second = svc.claim_step(ME, t.step_id)
    with pytest.raises(Rejected, match="token"):
        svc.heartbeat(ME, t.step_id, first.claim_token)
    assert svc.heartbeat(ME, t.step_id, second.claim_token).step_id == t.step_id
    ops = [e.operation for e in svc.get_task(ME, t.id).history]
    assert "lease_expired" in ops

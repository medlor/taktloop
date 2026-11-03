import pytest

from conftest import SHA

from taktloop.models import ArtifactIn
from taktloop.permissions import require
from taktloop.service import Caller, Rejected


def who(subject, *roles):
    return Caller(subject, frozenset(roles), "claude-code")


REQ, APPR, OWNER = who("tobias", "requester"), who("tobias", "approver"), who("tobias", "factory_owner")
A, B, REV, NOBODY = who("agent-a", "planner"), who("agent-b", "planner"), who("rev", "reviewer"), who("nobody")
ART = [ArtifactIn(name="design_doc", path="docs/d.md", commit=SHA, branch="main")]


def refused(call, *args, **kw):
    with pytest.raises(Rejected) as e:
        call(*args, **kw)
    return e.value.reason


def claimed_by_a(svc):
    t = svc.create_task(REQ, "T", "do it")
    return t, svc.claim_step(A, t.step_id)


def test_create_task_needs_requester(svc):
    assert refused(svc.create_task, A, "T") == "create_task requires role requester; you have planner"
    assert svc.create_task(REQ, "T").current_step == "design"


def test_reads_need_any_role(svc):
    t = svc.create_task(REQ, "T")
    for call, args in [(svc.list_tasks, ()), (svc.get_task, (t.id,)), (svc.list_open_steps, ()),
                       (svc.get_step_context, (t.step_id,))]:
        op = call.__name__
        assert refused(call, NOBODY, *args) == f"{op} requires a taktloop role; you have none"
        call(REV, *args)


def test_claim_needs_the_step_role_and_refusal_is_in_history(svc):
    t = svc.create_task(REQ, "T")
    assert refused(svc.claim_step, REQ, t.step_id) == (
        "claim_step requires role planner; you have requester")
    ev = svc.get_task(REQ, t.id).history[-1]
    assert (ev.operation, ev.result, ev.subject, ev.roles, ev.executor) == (
        "claim_step", "rejected", "tobias", ["requester"], "claude-code")
    assert svc.claim_step(A, t.step_id).claim_token


def test_roles_in_refusal_are_sorted(svc):
    t = svc.create_task(REQ, "T")
    caller = who("x", "reviewer", "approver")
    assert refused(svc.claim_step, caller, t.step_id) == (
        "claim_step requires role planner; you have approver, reviewer")


def test_heartbeat_and_complete_need_the_step_role(svc):
    t, c = claimed_by_a(svc)
    assert refused(svc.heartbeat, REQ, t.step_id, c.claim_token) == (
        "heartbeat requires role planner; you have requester")
    assert refused(svc.complete_step, REQ, t.step_id, c.claim_token, "completed", ART) == (
        "complete_step requires role planner; you have requester")
    svc.heartbeat(A, t.step_id, c.claim_token)
    assert svc.complete_step(A, t.step_id, c.claim_token, "completed", ART).current_step == (
        "design_approval")


def test_only_the_claimer_may_heartbeat_complete_or_release(svc):
    t, c = claimed_by_a(svc)
    msg = "step is claimed by agent-a, not you"
    assert refused(svc.heartbeat, B, t.step_id, c.claim_token) == msg
    assert refused(svc.complete_step, B, t.step_id, c.claim_token, "completed", ART) == msg
    assert refused(svc.release_step, B, t.step_id, "mine now", claim_token=c.claim_token) == msg
    assert svc.release_step(A, t.step_id, "done for today", claim_token=c.claim_token)


def test_release_needs_step_role_and_force_needs_factory_owner(svc):
    t, c = claimed_by_a(svc)
    assert refused(svc.release_step, REQ, t.step_id, "r", claim_token=c.claim_token) == (
        "release_step requires role planner; you have requester")
    assert refused(svc.release_step, A, t.step_id, "r", force=True) == (
        "release_step --force requires role factory_owner; you have planner")
    assert svc.release_step(OWNER, t.step_id, "session crashed", force=True).step_id == t.step_id


def test_decide_gate_needs_the_gate_role(svc):
    t, c = claimed_by_a(svc)
    svc.complete_step(A, t.step_id, c.claim_token, "completed", ART)
    assert refused(svc.decide_gate, A, t.id, None, "approve") == (
        "decide_gate requires role approver; you have planner")
    assert svc.decide_gate(APPR, t.id, None, "approve").current_step == "plan"


def test_override_review_needs_approver():
    assert refused(require, A, "override_review") == (
        "override_review requires role approver; you have planner")
    require(APPR, "override_review")


@pytest.mark.parametrize("op", ["sweep", "report_stall"])
def test_orchestrator_operations_need_orchestrator(op):
    assert refused(require, A, op) == f"{op} requires role orchestrator; you have planner"
    require(who("orchestrator", "orchestrator"), op)


def test_list_open_steps_only_shows_steps_of_the_callers_roles(svc):
    svc.create_task(REQ, "T")
    assert svc.list_open_steps(REV) == []
    assert [o.step for o in svc.list_open_steps(A)] == ["design"]


def test_claim_stores_subject_and_executor_and_waiting_for_names_both(svc, store):
    t, _ = claimed_by_a(svc)
    with store.transaction() as tx:
        run = tx.get_step_run(t.step_id)
    assert (run.claimed_by, run.claimed_executor) == ("agent-a", "claude-code")
    assert "in progress by agent-a (claude-code)" in svc.get_task(A, t.id).waiting_for


def test_caller_without_roles_is_refused_before_any_lookup_or_expiry(svc, store, clock):
    from datetime import timedelta

    t, c = claimed_by_a(svc)
    svc.complete_step(A, t.step_id, c.claim_token, "completed", ART)  # task 1 now at the gate
    t2, c2 = claimed_by_a(svc)
    clock.now += timedelta(minutes=31)  # t2's lease is overdue; a refused call must not expire it
    assert refused(svc.create_task, NOBODY, "T") == "create_task requires role requester; you have none"
    for sid, tid in [(t2.step_id, t.id), (99, 99)]:
        calls = [("claim_step", svc.claim_step, (sid,), {}),
                 ("heartbeat", svc.heartbeat, (sid, c2.claim_token), {}),
                 ("complete_step", svc.complete_step, (sid, c2.claim_token, "completed", ART), {}),
                 ("release_step", svc.release_step, (sid, "r"), {"claim_token": c2.claim_token}),
                 ("release_step", svc.release_step, (sid, "r"), {"force": True}),
                 ("decide_gate", svc.decide_gate, (tid, None, "approve"), {})]
        for op, call, args, kw in calls:
            assert refused(call, NOBODY, *args, **kw) == f"{op} requires a taktloop role; you have none"
    with store.transaction() as tx:
        assert tx.get_step_run(t2.step_id).status == "claimed"

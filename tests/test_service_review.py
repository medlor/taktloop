from pathlib import Path

import pytest

from taktloop.config import load_config
from taktloop.models import ArtifactIn
from taktloop.service import Rejected, Service
from test_service_steps import ME, SHA, clock, store  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
SHA2, SHA3 = "b" * 40, "c" * 40


@pytest.fixture
def svc(store, clock):
    return Service(store, load_config(ROOT / "factory.yaml").workflow, clock=clock)


def work(svc, tid, name, sha=SHA):
    t = svc.get_task(ME, tid)
    c = svc.claim_step(ME, t.step_id)
    return svc.complete_step(ME, t.step_id, c.claim_token, "completed",
                             [ArtifactIn(name=name, path=f"docs/{name}.md", commit=sha,
                                         branch="main")])


def review(svc, tid, outcome, findings=None):
    t = svc.get_task(ME, tid)
    assert t.current_step == "plan_review"
    c = svc.claim_step(ME, t.step_id)
    return svc.complete_step(ME, t.step_id, c.claim_token, outcome, [], findings)


def at_review(svc):
    tid = svc.create_task(ME, "T", "do it").id
    work(svc, tid, "design_doc")
    svc.decide_gate(ME, tid, None, "approve")
    s = work(svc, tid, "plan")
    assert s.waiting_for == "waiting for a reviewer to pick up plan_review"
    return tid


def context(svc, tid):
    return svc.get_step_context(ME, svc.get_task(ME, tid).step_id)


def test_pass_finishes_the_task(svc):
    tid = at_review(svc)
    s = review(svc, tid, "pass")
    assert s.status == "done"


def test_review_context_has_latest_plan_and_design(svc):
    tid = at_review(svc)
    ctx = context(svc, tid)
    assert ctx.feedback is None and ctx.required_artifacts == []
    assert [(a.name, a.round) for a in ctx.artifacts] == [("design_doc", 1), ("plan", 1)]
    review(svc, tid, "issues", "step 3 is vague")
    work(svc, tid, "plan", SHA2)
    ctx = context(svc, tid)
    assert [(a.name, a.round, a.commit) for a in ctx.artifacts] == [
        ("design_doc", 1, SHA), ("plan", 2, SHA2)]


def test_review_outcomes_are_checked(svc):
    tid = at_review(svc)
    t = svc.get_task(ME, tid)
    c = svc.claim_step(ME, t.step_id)
    for outcome, arts, findings, msg in [
        ("completed", [], None, "outcome must be 'pass' or 'issues'"),
        ("issues", [], None, "findings are required"),
        ("issues", [], "   ", "findings are required"),
        ("pass", [ArtifactIn(name="plan", path="p.md", commit=SHA, branch="main")], None,
         "review steps take no artifacts"),
    ]:
        with pytest.raises(Rejected, match=msg):
            svc.complete_step(ME, t.step_id, c.claim_token, outcome, arts, findings)
    assert svc.get_task(ME, tid).current_step == "plan_review"


def test_issues_reopen_plan_with_findings_then_escalate(svc, store):
    tid = at_review(svc)
    s = review(svc, tid, "issues", "  step 3 is vague  ")
    assert s.current_step == "plan"
    ctx = context(svc, tid)
    assert ctx.round == 2
    assert (ctx.feedback.kind, ctx.feedback.text, ctx.feedback.by) == (
        "review_issues", "step 3 is vague", "local")
    with store.transaction() as tx:
        runs = [r for r in tx.list_step_runs(tid) if r.step == "plan_review"]
    assert runs[0].evidence == {"findings": "step 3 is vague"}
    work(svc, tid, "plan", SHA2)
    assert context(svc, tid).feedback is None  # review round 2 was opened by plan, not a verdict
    s = review(svc, tid, "issues", "still vague")
    assert s.current_step == "plan_escalation"
    assert s.waiting_for == (
        "waiting for approver decision at plan_escalation (plan failed review 2 times)")


def test_issue_count_restarts_after_escalation_gate(svc):
    """Review Focus 2."""
    tid = at_review(svc)
    review(svc, tid, "issues", "one")
    work(svc, tid, "plan", SHA2)
    review(svc, tid, "issues", "two")
    svc.decide_gate(ME, tid, "plan_escalation", "reject", "try again")  # default: plan
    ctx = context(svc, tid)
    assert (ctx.step, ctx.round) == ("plan", 3)
    assert (ctx.feedback.kind, ctx.feedback.text) == ("gate_rejection", "try again")
    work(svc, tid, "plan", SHA3)
    assert review(svc, tid, "issues", "three").current_step == "plan"
    work(svc, tid, "plan", SHA3)
    assert review(svc, tid, "issues", "four").current_step == "plan_escalation"


def test_escalation_reject_to_design(svc):
    tid = at_review(svc)
    review(svc, tid, "issues", "one")
    work(svc, tid, "plan", SHA2)
    review(svc, tid, "issues", "two")
    s = svc.decide_gate(ME, tid, None, "reject", "rethink it", target="design")
    assert s.current_step == "design"
    ctx = context(svc, tid)
    assert ctx.round == 2
    assert (ctx.feedback.kind, ctx.feedback.text, ctx.feedback.by) == (
        "gate_rejection", "rethink it", "local")


def test_escalation_reject_to_other_step_is_refused(svc):
    tid = at_review(svc)
    review(svc, tid, "issues", "one")
    work(svc, tid, "plan", SHA2)
    review(svc, tid, "issues", "two")
    with pytest.raises(Rejected) as exc:
        svc.decide_gate(ME, tid, None, "reject", "no", target="plan_review")
    assert exc.value.reason == "gate plan_escalation can send back to plan, design; not plan_review"
    assert svc.get_task(ME, tid).current_step == "plan_escalation"


def test_escalation_approve_finishes(svc):
    tid = at_review(svc)
    review(svc, tid, "issues", "one")
    work(svc, tid, "plan", SHA2)
    review(svc, tid, "issues", "two")
    assert svc.decide_gate(ME, tid, None, "approve").status == "done"

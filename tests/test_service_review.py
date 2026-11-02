from pathlib import Path

import pytest
import yaml

from taktloop.config import load_config
from taktloop.models import ArtifactIn
from taktloop.service import Caller, Rejected, Service
from taktloop.workflow import parse_workflow
from conftest import ME, SHA

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


def test_pass_opens_the_workspace(svc):
    tid = at_review(svc)
    s = review(svc, tid, "pass")
    assert s.current_step == "workspace"


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
    verdicts = [e for e in svc.get_task(ME, tid).history
                if e.operation == "complete_step" and e.step == "plan_review"]
    assert [e.detail["next"] for e in verdicts] == ["plan", "plan_escalation"]
    hist = svc.get_task(ME, tid).history
    esc = [e for e in hist if e.operation == "escalate"]
    assert len(esc) == 1 and hist[-1] is esc[0]  # recorded right after the verdict
    assert (esc[0].subject, esc[0].roles, esc[0].step, esc[0].detail) == (
        "core", [], "plan_escalation",
        {"review_step": "plan_review", "gate": "plan_escalation", "issue_rounds": 2})


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


def test_issue_count_restarts_after_pass(store, clock):
    wf = parse_workflow(yaml.safe_load("""
start: plan
steps:
  plan: {kind: agent, role: planner, skill: s, output: {artifacts: [plan]}, next: plan_review}
  plan_review: {kind: agent, role: reviewer, skill: s, review_of: plan, on_pass: gate,
                on_issues: plan, max_issue_rounds: 2, on_escalate: plan_escalation}
  gate: {kind: human_gate, role: approver, on_approve: done, on_reject: plan}
  plan_escalation: {kind: human_gate, role: approver, on_approve: done, on_reject: plan}
"""))
    svc = Service(store, wf, clock=clock)
    tid = svc.create_task(ME, "T").id
    work(svc, tid, "plan")
    for outcome in ("issues", "pass"):
        t = svc.get_task(ME, tid)
        c = svc.claim_step(ME, t.step_id)
        svc.complete_step(ME, t.step_id, c.claim_token, outcome, [], "f" if outcome == "issues" else None)
        if outcome == "issues":
            work(svc, tid, "plan", SHA2)
    svc.decide_gate(ME, tid, "gate", "reject", "again")
    work(svc, tid, "plan", SHA3)
    t = svc.get_task(ME, tid)
    c = svc.claim_step(ME, t.step_id)
    s = svc.complete_step(ME, t.step_id, c.claim_token, "issues", [], "one")
    assert s.current_step == "plan"


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


def test_escalation_approve_opens_the_workspace(svc):
    tid = at_review(svc)
    review(svc, tid, "issues", "one")
    work(svc, tid, "plan", SHA2)
    review(svc, tid, "issues", "two")
    assert svc.decide_gate(ME, tid, None, "approve").current_step == "workspace"


# --- override_review (spec 6.3) ------------------------------------------------

AGENT_B = Caller("agent-b", frozenset({"reviewer"}), "codex")


def runs_of(store, tid, step):
    with store.transaction() as tx:
        return [r for r in tx.list_step_runs(tid) if r.step == step]


def override_event(svc, tid):
    [e] = [e for e in svc.get_task(ME, tid).history if e.operation == "override_review"]
    return e


def two_issues(svc):
    tid = at_review(svc)
    review(svc, tid, "issues", "one")
    work(svc, tid, "plan", SHA2)
    review(svc, tid, "issues", "two")
    return tid


def test_override_at_open_review_step(svc, store):
    tid = at_review(svc)
    s = svc.override_review(ME, tid, "plan_review", "  good enough  ")
    assert s.current_step == "workspace"
    [run] = runs_of(store, tid, "plan_review")
    assert (run.status, run.outcome) == ("completed", "superseded")
    e = override_event(svc, tid)
    assert e.result == "ok" and e.step == "plan_review"
    assert (e.detail["reason"], e.detail["review_step"], e.detail["verdicts"]) == (
        "good enough", "plan_review", [])
    assert [(a["name"], a["round"], a["commit"]) for a in e.detail["approved"]] == [
        ("plan", 1, SHA)]


def test_override_cancels_a_claim_at_the_review_step(svc, store):
    tid = at_review(svc)
    t = svc.get_task(ME, tid)
    c = svc.claim_step(AGENT_B, t.step_id)
    assert svc.override_review(ME, tid, "plan_review", "ship it").current_step == "workspace"
    with pytest.raises(Rejected, match="step is not claimed"):
        svc.complete_step(AGENT_B, t.step_id, c.claim_token, "pass", [])
    [run] = runs_of(store, tid, "plan_review")
    assert (run.status, run.outcome) == ("completed", "superseded")
    assert override_event(svc, tid).detail["cancelled_claim"] == "agent-b (codex)"


def test_override_at_escalation_gate(svc, store):
    tid = two_issues(svc)
    assert svc.get_task(ME, tid).current_step == "plan_escalation"
    assert svc.override_review(ME, tid, "plan_review", "fine").current_step == "workspace"
    [gate] = runs_of(store, tid, "plan_escalation")
    assert gate.outcome == "superseded"
    e = override_event(svc, tid)
    assert e.detail["verdicts"] == [{"outcome": "issues", "by": "local", "round": 1},
                                    {"outcome": "issues", "by": "local", "round": 2}]
    assert [(a["round"], a["commit"]) for a in e.detail["approved"]] == [(2, SHA2)]


def test_override_during_plan_revision_approves_latest_completed_round(svc, store):
    """Review Focus 3."""
    tid = two_issues(svc)
    svc.decide_gate(ME, tid, "plan_escalation", "reject", "try again")
    t = svc.get_task(ME, tid)
    assert (t.current_step, context(svc, tid).round) == ("plan", 3)
    assert svc.override_review(ME, tid, "plan_review", "round 2 is fine").current_step == "workspace"
    e = override_event(svc, tid)
    assert e.step == "plan"
    assert [(a["name"], a["round"], a["path"], a["commit"]) for a in e.detail["approved"]] == [
        ("plan", 2, "docs/plan.md", SHA2)]
    plans = runs_of(store, tid, "plan")
    assert [(r.round, r.outcome) for r in plans] == [
        (1, "completed"), (2, "completed"), (3, "superseded")]
    assert [r.outcome for r in runs_of(store, tid, "plan_review")] == ["issues", "issues"]
    assert [r.evidence for r in runs_of(store, tid, "plan_review")] == [
        {"findings": "one"}, {"findings": "two"}]


def test_override_before_any_review_is_rejected(svc):
    tid = svc.create_task(ME, "T", "do it").id
    work(svc, tid, "design_doc")
    svc.decide_gate(ME, tid, None, "approve")
    assert svc.get_task(ME, tid).current_step == "plan"
    with pytest.raises(Rejected) as exc:
        svc.override_review(ME, tid, "plan_review", "skip")
    assert exc.value.reason == f"task {tid} has no review of plan to override"
    e = override_event(svc, tid)
    assert (e.result, e.reason) == ("rejected", exc.value.reason)


def test_override_needs_approver_and_a_reason(svc):
    tid = at_review(svc)
    planner = Caller("p", frozenset({"planner"}))
    with pytest.raises(Rejected) as exc:
        svc.override_review(planner, tid, "plan_review", "mine")
    assert exc.value.reason == "override_review requires role approver; you have planner"
    e = override_event(svc, tid)
    assert (e.subject, e.roles, e.result, e.reason) == ("p", ["planner"], "rejected", exc.value.reason)
    with pytest.raises(Rejected, match="reason is required"):
        svc.override_review(ME, tid, "plan_review", "  ")
    with pytest.raises(Rejected, match="plan is not a review step"):
        svc.override_review(ME, tid, "plan", "x")
    assert svc.get_task(ME, tid).current_step == "plan_review"

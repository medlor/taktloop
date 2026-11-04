import pytest
import yaml

from conftest import ALL_ROLES, SHA
from taktloop.config import load_config
from taktloop.models import ArtifactIn
from taktloop.separation import Record, approved_step, check_approval, check_claim
from taktloop.service import Caller, Rejected, Service
from taktloop.workflow import parse_workflow
from test_service_review import M3B

WF = load_config(M3B / "factory.yaml").workflow
A, B, T = (Caller(s, frozenset((*ALL_ROLES, "builder", "verifier"))) for s in ("agent-a", "agent-b", "tobias"))


def make(store, clock, separation):
    return Service(store, WF, clock=clock, separation=separation)


@pytest.fixture
def strict(store, clock):
    return make(store, clock, "strict")


@pytest.fixture
def off(store, clock):
    return make(store, clock, "off")


def step_id(svc, tid):
    return svc.get_task(T, tid).step_id


def do(svc, who, tid, outcome="completed", name=None, findings=None):
    sid = step_id(svc, tid)
    c = svc.claim_step(who, sid)
    arts = [ArtifactIn(name=name, path=f"docs/{name}.md", commit=SHA, branch="main")] if name else []
    return svc.complete_step(who, sid, c.claim_token, outcome, arts, findings)


def refusals(svc, tid):
    return [(e.operation, e.subject, e.reason) for e in svc.get_task(T, tid).history
            if e.result == "rejected"]


def to_plan_review(svc, planner=A, designer=A, approver=T):
    tid = svc.create_task(T, "T", "do it").id
    do(svc, designer, tid, name="design_doc")
    svc.decide_gate(approver, tid, None, "approve")
    do(svc, planner, tid, name="plan")
    return tid


def test_planner_cannot_review_own_plan(strict):
    tid = to_plan_review(strict)
    with pytest.raises(Rejected) as e:
        strict.claim_step(A, step_id(strict, tid))
    assert e.value.reason == f"strict separation: agent-a completed plan round 1 of task {tid}"
    [ev] = [e for e in strict.get_task(T, tid).history if e.result == "rejected"]
    assert ev.operation == "claim_step" and ev.reason == e.value.reason
    strict.claim_step(B, step_id(strict, tid))


def test_reviewer_cannot_claim_next_plan_round(strict):
    tid = to_plan_review(strict)
    do(strict, B, tid, "issues", findings="vague")
    with pytest.raises(Rejected, match=f"agent-b completed plan_review round 1 of task {tid}"):
        strict.claim_step(B, step_id(strict, tid))
    strict.claim_step(A, step_id(strict, tid))


def test_designer_cannot_decide_design_approval(strict):
    tid = strict.create_task(T, "T").id
    do(strict, T, tid, name="design_doc")
    with pytest.raises(Rejected, match=f"tobias completed design round 1 of task {tid}"):
        strict.decide_gate(T, tid, None, "approve")
    with pytest.raises(Rejected, match="strict separation"):
        strict.decide_gate(T, tid, None, "reject", "no")
    assert strict.get_task(T, tid).current_step == "design_approval"
    why = f"strict separation: tobias completed design round 1 of task {tid}"
    assert refusals(strict, tid) == [("decide_gate", "tobias", why)] * 2
    strict.decide_gate(B, tid, None, "approve")


def test_planner_cannot_decide_escalation_gate(strict):
    tid = to_plan_review(strict, planner=T, approver=B)
    do(strict, A, tid, "issues", findings="vague")
    do(strict, T, tid, name="plan")
    do(strict, A, tid, "issues", findings="still vague")
    assert strict.get_task(T, tid).current_step == "plan_escalation"
    with pytest.raises(Rejected, match=f"tobias completed plan round 1 of task {tid}"):
        strict.decide_gate(T, tid, None, "approve")
    assert strict.decide_gate(B, tid, None, "approve").current_step == "workspace"


def test_planner_cannot_override(strict):
    tid = to_plan_review(strict, planner=T, approver=B)
    with pytest.raises(Rejected, match=f"tobias completed plan round 1 of task {tid}"):
        strict.override_review(T, tid, "plan_review", "ship it")
    assert refusals(strict, tid) == [
        ("override_review", "tobias", f"strict separation: tobias completed plan round 1 of task {tid}")]
    assert strict.override_review(B, tid, "plan_review", "ship it").current_step == "workspace"


def test_approved_step():
    assert approved_step(WF, WF.step("design_approval"), None) == "design"
    assert approved_step(WF, WF.step("plan_escalation"), None) == "plan"
    assert approved_step(WF, None, WF.step("plan_review")) == "plan"


def test_rules_name_the_earliest_record():
    recs = [Record("plan", 1, "agent-a", 3), Record("plan", 2, "agent-a", 3)]
    want = "strict separation: agent-a completed plan round 1 of task 3"
    assert check_claim(WF, WF.step("plan_review"), "agent-a", recs) == want
    assert check_approval("plan", "agent-a", recs) == want
    assert check_claim(WF, WF.step("plan_review"), "agent-b", recs) is None
    assert check_claim(WF, WF.step("design"), "agent-a", recs) is None
    assert check_approval("design", "agent-a", recs) is None


def test_off_allows_one_identity_to_do_everything(off):
    tid = to_plan_review(off)
    do(off, A, tid, "issues", findings="vague")
    do(off, A, tid, name="plan")
    assert do(off, A, tid, "pass").current_step == "workspace"
    tid = to_plan_review(off, planner=A, designer=A, approver=A)
    assert off.override_review(A, tid, "plan_review", "fine").current_step == "workspace"


def test_superseded_run_is_not_a_completion(store, clock):
    """A plan round cut short by an override was never completed: its claimer may still approve."""
    raw = yaml.safe_load((M3B / "workflow.yaml").read_text())
    raw["steps"]["plan_review"]["on_pass"] = "release"
    raw["steps"]["release"] = {"kind": "human_gate", "role": "approver", "on_approve": "done",
                               "on_reject": "plan"}
    svc = Service(store, parse_workflow(raw), clock=clock, separation="strict")
    tid = to_plan_review(svc, planner=T, designer=T, approver=B)
    do(svc, B, tid, "issues", findings="vague")
    svc.claim_step(A, step_id(svc, tid))  # plan round 2, cut short by the override
    svc.override_review(B, tid, "plan_review", "round 1 is fine")
    assert svc.get_task(T, tid).current_step == "release"
    assert svc.decide_gate(A, tid, None, "approve").status == "done"


def test_integration_gate_approves_build(store, clock):
    wf = parse_workflow(yaml.safe_load("""
start: build
steps:
  build: {kind: agent, role: builder, skill: s, output: {artifacts: [change]}, next: verify}
  verify:
    kind: agent
    role: verifier
    skill: s
    evidence: [ci_run]
    check: {mcp_server: github, tool: t, args: {}, expect: {ok: "yes"}}
    next: gate
    on_fail: build
  gate: {kind: human_gate, role: approver, approves: build, on_approve: done, on_reject: build}
"""))
    assert approved_step(wf, wf.step("gate"), None) == "build"
    svc = Service(store, wf, clock=clock, separation="strict",
                  checker=lambda server, tool, args: {"ok": "yes"})
    tid = svc.create_task(A, "T").id
    do(svc, A, tid, name="change")
    sid = step_id(svc, tid)
    c = svc.claim_step(B, sid)
    svc.complete_step(B, sid, c.claim_token, "completed", [], evidence={"ci_run": "1"})
    with pytest.raises(Rejected) as e:
        svc.decide_gate(A, tid, "gate", "approve")
    assert e.value.reason == f"strict separation: agent-a completed build round 1 of task {tid}"


def test_approved_step_uses_approves_not_file_order():
    wf = parse_workflow(yaml.safe_load("""
start: gate
steps:
  gate: {kind: human_gate, role: approver, approves: design, on_approve: design, on_reject: design}
  other: {kind: human_gate, role: approver, on_approve: done, on_reject: design}
  design: {kind: agent, role: planner, skill: s, output: {artifacts: [d]}, next: other}
"""))
    assert approved_step(wf, wf.step("gate"), None) == "design"
    assert approved_step(wf, wf.step("other"), None) == ""

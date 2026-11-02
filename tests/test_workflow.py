import copy

import pytest
import yaml

from taktloop.workflow import DONE, WorkflowError, parse_workflow

MVP = yaml.safe_load(
    """
start: design
steps:
  design:
    kind: agent
    role: planner
    skill: brainstorming
    instructions: >
      Run brainstorming with the requester until the design doc is written
      and the requester is happy with it. Do not invoke writing-plans.
    output:
      artifacts: [design_doc]
    next: design_approval
  design_approval:
    kind: human_gate
    role: approver
    on_approve: plan
    on_reject: design
  plan:
    kind: agent
    role: planner
    skill: writing-plans
    instructions: >
      Write the plan from the approved design doc. Do not ask for an
      execution mode and do not start executing.
    output:
      artifacts: [plan]
    next: done
"""
)


def mvp():
    return copy.deepcopy(MVP)


def test_parses_mvp_workflow():
    wf = parse_workflow(mvp())
    assert wf.start == "design"
    assert wf.step("design").required_artifacts == ("design_doc",)
    assert wf.step("design").skill == "brainstorming"
    gate = wf.step("design_approval")
    assert gate.kind == "human_gate"
    assert gate.on_approve == "plan"
    assert gate.on_reject == ("design",)
    assert gate.skill is None and gate.next is None
    assert gate.required_artifacts == ()
    assert wf.step("plan").next == DONE
    assert wf.step("plan").on_approve is None


def test_rejects_unknown_start():
    data = mvp()
    data["start"] = "ghost"
    with pytest.raises(WorkflowError, match="ghost"):
        parse_workflow(data)


def test_rejects_dangling_next():
    data = mvp()
    data["steps"]["design"]["next"] = "nowhere"
    with pytest.raises(WorkflowError, match="nowhere"):
        parse_workflow(data)


def test_rejects_unreachable_step():
    data = mvp()
    data["steps"]["orphan"] = dict(data["steps"]["plan"])
    with pytest.raises(WorkflowError, match="orphan"):
        parse_workflow(data)


def test_rejects_unsupported_kind():
    data = mvp()
    data["steps"]["plan"]["kind"] = "code"
    with pytest.raises(WorkflowError, match="not supported yet") as exc:
        parse_workflow(data)
    assert "plan" in str(exc.value)


def test_agent_step_requires_skill_and_output():
    data = mvp()
    del data["steps"]["plan"]["skill"]
    with pytest.raises(WorkflowError, match="plan"):
        parse_workflow(data)
    data = mvp()
    del data["steps"]["plan"]["output"]
    with pytest.raises(WorkflowError, match="plan"):
        parse_workflow(data)


def test_gate_requires_on_approve_and_on_reject():
    for key in ("on_approve", "on_reject"):
        data = mvp()
        del data["steps"]["design_approval"][key]
        with pytest.raises(WorkflowError, match="design_approval"):
            parse_workflow(data)


def test_step_requires_role():
    data = mvp()
    del data["steps"]["plan"]["role"]
    with pytest.raises(WorkflowError, match="plan"):
        parse_workflow(data)


def test_step_named_done_is_reserved():
    data = mvp()
    data["steps"]["done"] = data["steps"]["plan"]
    with pytest.raises(WorkflowError, match="done"):
        parse_workflow(data)


REVIEW = yaml.safe_load(
    """
plan_review:
  kind: agent
  role: reviewer
  skill: taktloop-plan-review
  instructions: >
    Review the plan against the approved design doc. Return pass or
    issues with concrete findings. Do not edit the plan.
  review_of: plan
  on_pass: done
  on_issues: plan
  max_issue_rounds: 2
  on_escalate: plan_escalation
plan_escalation:
  kind: human_gate
  role: approver
  on_approve: done
  on_reject: [plan, design]
"""
)


def reviewed():
    data = mvp()
    data["steps"]["plan"]["next"] = "plan_review"
    data["steps"].update(copy.deepcopy(REVIEW))
    return data


def test_parses_review_workflow():
    wf = parse_workflow(reviewed())
    r = wf.step("plan_review")
    assert r.is_review and not wf.step("plan").is_review
    assert (r.review_of, r.on_pass, r.on_issues, r.max_issue_rounds, r.on_escalate) == (
        "plan", DONE, "plan", 2, "plan_escalation")
    assert r.required_artifacts == () and r.next is None
    assert wf.step("plan_escalation").on_reject == ("plan", "design")
    assert wf.step("design_approval").on_reject == ("design",)
    assert set(r.targets) >= {"plan", DONE, "plan_escalation"}


@pytest.mark.parametrize("drop", ["review_of", "on_pass", "on_issues"])
def test_review_step_needs_review_of_on_pass_on_issues(drop):
    data = reviewed()
    del data["steps"]["plan_review"][drop]
    with pytest.raises(WorkflowError) as exc:
        parse_workflow(data)
    assert str(exc.value) == "step 'plan_review': review steps need review_of, on_pass and on_issues"


@pytest.mark.parametrize("drop", ["max_issue_rounds", "on_escalate"])
def test_max_issue_rounds_and_on_escalate_go_together(drop):
    data = reviewed()
    del data["steps"]["plan_review"][drop]
    with pytest.raises(WorkflowError) as exc:
        parse_workflow(data)
    assert str(exc.value) == "step 'plan_review': max_issue_rounds and on_escalate go together"


@pytest.mark.parametrize("target", ["design_approval", "plan_review", "ghost"])
def test_review_of_must_name_an_agent_work_step(target):
    data = reviewed()
    data["steps"]["plan_review"]["review_of"] = target
    with pytest.raises(WorkflowError) as exc:
        parse_workflow(data)
    assert str(exc.value) == "step 'plan_review': review_of must name an agent work step"


@pytest.mark.parametrize("extra", [{"next": "done"}, {"output": {"artifacts": ["x"]}}])
def test_review_step_has_no_output_or_next(extra):
    data = reviewed()
    data["steps"]["plan_review"].update(extra)
    with pytest.raises(WorkflowError, match="plan_review"):
        parse_workflow(data)


def test_on_reject_list_targets_must_exist():
    data = reviewed()
    data["steps"]["plan_escalation"]["on_reject"] = ["plan", "nowhere"]
    with pytest.raises(WorkflowError, match="nowhere"):
        parse_workflow(data)

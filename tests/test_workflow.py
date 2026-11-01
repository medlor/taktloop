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
    assert gate.on_reject == "design"
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

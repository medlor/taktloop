import copy

import pytest
import yaml

from taktloop.workflow import DONE, WorkflowError, graph_problems, parse_step, parse_workflow

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
    data["steps"]["plan"]["kind"] = "robot"
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


def _verify_workflow(**verify_changes):
    verify = {
        "kind": "agent", "role": "verifier", "skill": "verification-before-completion",
        "instructions": "Wait for CI.",
        "evidence": ["ci_run"],
        "check": {
            "mcp_server": "github", "tool": "actions_get",
            "args": {"resource_id": "{{evidence.ci_run}}"},
            "match": {"workflow_run.head_sha": "{{artifacts.change.commit}}"},
            "expect": {"workflow_run.conclusion": "success"},
        },
        "next": "done", "on_fail": "work",
    }
    verify.update(verify_changes)
    verify = {k: v for k, v in verify.items() if v is not None}
    return {"start": "work", "steps": {
        "work": {"kind": "agent", "role": "builder", "skill": "s", "instructions": "i",
                 "output": {"artifacts": ["change"]}, "next": "verify"},
        "verify": verify,
    }}


def test_check_step_loads():
    step = parse_workflow(_verify_workflow()).step("verify")
    assert step.evidence == ("ci_run",)
    assert step.check.tool == "actions_get"
    assert "work" in step.targets


def _with_check(**check_changes):
    base = _verify_workflow()["steps"]["verify"]["check"]
    return _verify_workflow(check={**base, **check_changes})


def _on_review():
    wf = _verify_workflow()
    wf["steps"]["review"] = {
        "kind": "agent", "role": "reviewer", "skill": "s", "instructions": "i",
        "review_of": "work", "on_pass": "verify", "on_issues": "work",
        "check": wf["steps"]["verify"]["check"],
    }
    wf["steps"]["work"]["next"] = "review"
    return wf


def _on_gate():
    wf = _verify_workflow()
    wf["steps"]["gate"] = {"kind": "human_gate", "role": "approver", "on_approve": "verify",
                           "on_reject": "work", "check": wf["steps"]["verify"]["check"]}
    wf["steps"]["work"]["next"] = "gate"
    return wf


@pytest.mark.parametrize("wf, msg", [
    (_verify_workflow(on_fail=None), "check, evidence and on_fail go together"),
    (_verify_workflow(check=None), "check, evidence and on_fail go together"),
    (_with_check(args={"resource_id": "{{evidence.run}}"}), "unknown placeholder evidence.run"),
    (_with_check(match={"a": "{{artifacts.nope.commit}}"}), "unknown placeholder artifacts.nope.commit"),
    (_with_check(match={"a": "{{artifacts.change.sha}}"}), "unknown placeholder artifacts.change.sha"),
    (_with_check(match={"a": "{{task.nope}}"}), "unknown placeholder task.nope"),
    (_on_review(), "checks are allowed on agent work steps only"),
    (_on_gate(), "checks are allowed on agent work steps only"),
    (_verify_workflow(on_fail="nowhere"), "points to unknown step"),
    (_with_check(tool=None), "tool"),
])
def test_check_step_refusals(wf, msg):
    with pytest.raises(WorkflowError, match=msg):
        parse_workflow(wf)


def test_check_step_needs_no_artifacts():
    step = parse_workflow(_verify_workflow()).step("verify")
    assert step.required_artifacts == ()


def _code_workflow(**changes):
    verify = {
        "kind": "code", "role": "verifier",
        "call": {"mcp_server": "github", "tool": "actions_list",
                 "args": {"workflow_runs_filter": {"branch": "{{artifacts.change.branch}}"}},
                 "evidence": {"ci_run": "workflow_runs.workflow_runs.0.id"}},
        "check": _verify_workflow()["steps"]["verify"]["check"],
        "evidence": ["ci_run"], "next": "done", "on_fail": "work",
    }
    verify.update(changes)
    wf = _verify_workflow()
    wf["steps"]["verify"] = {k: v for k, v in verify.items() if v is not None}
    return wf


def test_code_step_loads():
    step = parse_workflow(_code_workflow()).step("verify")
    assert step.kind == "code"
    assert step.call.tool == "actions_list"
    assert step.evidence == ("ci_run",)
    assert step.check is not None
    assert "work" in step.targets


@pytest.mark.parametrize("changes", [
    {"skill": "s"},
    {"instructions": "Do it."},
    {"output": {"artifacts": ["x"]}},
    {"next": None},
    {"call": None},
    {"evidence": ["other"]},
    {"call": {"mcp_server": "github", "tool": "t", "args": {"a": ["{{evidence.ci_run}}"]},
              "evidence": {"ci_run": "id"}}},
    {"dispatch": "push"},
])
def test_code_step_refusals(changes):
    with pytest.raises(WorkflowError, match="verify"):
        parse_workflow(_code_workflow(**changes))


def _push_workflow(**push_changes):
    wf = reviewed()
    wf["steps"]["plan_review"] = {
        **wf["steps"]["plan_review"],
        "dispatch": "push", "push": {"start": "claude-headless", "identity": "reviewer-bot"},
    }
    wf["steps"]["plan_review"]["push"].update(push_changes)
    return wf


def test_push_step_loads():
    step = parse_workflow(_push_workflow()).step("plan_review")
    assert (step.dispatch, step.push_identity) == ("push", "reviewer-bot")
    assert parse_workflow(reviewed()).step("plan_review").dispatch == "pull"


@pytest.mark.parametrize("wf, msg", [
    (_push_workflow(start="codex"), "not supported"),
    (_push_workflow(identity=""), "push.identity"),
    (_push_workflow(identity=None), "push.identity"),
])
def test_push_refusals(wf, msg):
    with pytest.raises(WorkflowError, match=msg):
        parse_workflow(wf)


def test_dispatch_refusals():
    wf = reviewed()
    wf["steps"]["plan_escalation"]["dispatch"] = "push"
    with pytest.raises(WorkflowError, match="plan_escalation"):
        parse_workflow(wf)
    wf = reviewed()
    wf["steps"]["plan_review"]["dispatch"] = "sometimes"
    with pytest.raises(WorkflowError, match="dispatch"):
        parse_workflow(wf)


def test_routes_on_agent_step_and_gate():
    wf = parse_workflow({"start": "triage", "steps": {
        "triage": {"kind": "agent", "role": "triage", "skill": "taktloop-triage",
                   "routes": {"quick": "build", "park": "decide"}},
        "decide": {"kind": "human_gate", "role": "approver",
                   "routes": {"quick": "build", "cancel": "cancelled"}},
        "build": {"kind": "agent", "role": "builder", "skill": "s",
                  "output": {"artifacts": ["change"]}, "next": "done",
                  "description": "Builds it."}}})
    assert wf.steps["triage"].routes == {"quick": "build", "park": "decide"}
    assert set(wf.steps["decide"].targets) == {"build", "cancelled"}
    assert wf.steps["build"].description == "Builds it."
    assert wf.data["start"] == "triage" and wf.version == 1


CALL = {"mcp_server": "m", "tool": "t", "evidence": {"k": "id"}}


@pytest.mark.parametrize("raw, msg", [
    ({"kind": "agent", "role": "r", "skill": "s", "routes": {}}, "routes must map"),
    ({"kind": "agent", "role": "r", "skill": "s", "routes": {"Quick!": "x"}}, "routes must map"),
    ({"kind": "agent", "role": "r", "skill": "s", "routes": {"q": "x"}, "next": "x"}, "routing steps take no next"),
    ({"kind": "human_gate", "role": "r", "routes": {"q": "x"}, "on_approve": "x", "on_reject": "x"},
     "take no on_approve or on_reject"),
    ({"kind": "human_gate", "role": "r", "routes": {"q": "x"}, "approves": "x"}, "only on gates without routes"),
    ({"kind": "code", "role": "r", "call": CALL, "next": "done", "routes": {"q": "x"}}, "code steps take no routes"),
    ({"kind": "code", "role": "r", "call": CALL, "next": "done", "approves": "x"}, "code steps take no approves"),
])
def test_parse_step_refuses_bad_routes(raw, msg):
    with pytest.raises(WorkflowError, match=msg):
        parse_step("s", raw)


def _steps(**over):
    raw = {"w": {"kind": "agent", "role": "r", "skill": "s", "output": {"artifacts": ["x"]}, "next": "c"},
           "r": {"kind": "agent", "role": "r", "skill": "s", "review_of": "w", "on_pass": "c", "on_issues": "w"},
           "c": {"kind": "code", "role": "r", "call": CALL, "next": "done"}, **over}
    return {n: parse_step(n, v) for n, v in raw.items()}


@pytest.mark.parametrize("ref, ok", [("evidence.c.k", True), ("evidence.nope.k", False), ("evidence.c.zz", False)])
def test_call_args_evidence_placeholder(ref, ok):
    call = {**CALL, "args": {"a": "{{%s}}" % ref}}
    steps = _steps(d={"kind": "code", "role": "r", "call": call, "next": "done"})
    assert any("unknown placeholder" in p for p in graph_problems(steps, "w")) is not ok


@pytest.mark.parametrize("target", ["r", "c"])
def test_approves_must_name_agent_work_step_with_artifacts(target):
    steps = _steps(g={"kind": "human_gate", "role": "r", "on_approve": "done", "on_reject": "w",
                      "approves": target})
    assert any("approves" in p for p in graph_problems(steps, "w"))


def test_data_is_a_detached_copy_without_version():
    raw = {"start": "a", "steps": {"a": {"kind": "agent", "role": "r", "skill": "s",
                                         "output": {"artifacts": ["x"]}, "next": "done"}}}
    wf = parse_workflow(raw, version=3)
    assert wf.data == raw and wf.version == 3 and "version" not in wf.data
    raw["steps"]["a"]["next"] = "changed"
    assert wf.data["steps"]["a"]["next"] == "done"


def test_unhashable_start_is_a_problem():
    assert graph_problems(_steps(), ["w"])[0].startswith("start")


def test_graph_problems_are_listed_not_raised():
    steps = {"a": parse_step("a", {"kind": "agent", "role": "r", "skill": "s",
                                   "output": {"artifacts": ["x"]}, "next": "b"}),
             "c": parse_step("c", {"kind": "human_gate", "role": "r", "on_approve": "done",
                                   "on_reject": "a", "approves": "zzz"})}
    problems = graph_problems(steps, "a")
    assert any("unknown step 'b'" in p for p in problems)
    assert any("unreachable" in p and "c" in p for p in problems)
    assert any("approves" in p for p in problems)
    assert graph_problems(steps, None)[0].startswith("start")


def test_cancelled_is_reserved():
    with pytest.raises(WorkflowError):
        parse_step("cancelled", {"kind": "agent", "role": "r", "skill": "s",
                                 "output": {"artifacts": ["x"]}, "next": "done"})


def test_task_placeholders_allowed_in_call_args():
    parse_workflow({"start": "t", "steps": {"t": {"kind": "code", "role": "steward",
        "call": {"mcp_server": "tickets", "tool": "create_ticket",
                 "args": {"title": "{{task.title}}", "body": "{{task.request}}"},
                 "evidence": {"ticket": "id"}}, "next": "done"}}})


def test_approves_target_without_artifacts_is_a_problem():
    steps = _steps(t={"kind": "agent", "role": "r", "skill": "s", "routes": {"q": "c"}},
                   g={"kind": "human_gate", "role": "r", "on_approve": "done", "on_reject": "w", "approves": "t"})
    assert not steps["t"].required_artifacts and not steps["t"].is_review
    assert any("approves 't'" in p for p in graph_problems(steps, "w"))

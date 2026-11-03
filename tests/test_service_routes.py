import pytest

from conftest import SHA

from taktloop.models import ArtifactIn
from taktloop.service import Caller, Rejected, Service
from taktloop.workflow import parse_workflow

REQ = Caller("tobias", frozenset({"requester"}))
APPROVER_REQ = Caller("boss", frozenset({"requester", "approver"}))
TRIAGE = Caller("triage-bot", frozenset({"triage"}))
BUILDER = Caller("agent-a", frozenset({"builder"}))
ORCH = Caller("orchestrator", frozenset({"orchestrator"}))

WORKFLOW = {"start": "triage", "steps": {
    "triage": {"kind": "agent", "role": "triage", "skill": "s", "instructions": "i",
               "description": "Pick a route", "routes": {"quick": "build", "park": "decide"}},
    "decide": {"kind": "human_gate", "role": "approver",
               "routes": {"cancel": "cancelled", "again": "triage"}},
    "build": {"kind": "agent", "role": "builder", "skill": "s", "instructions": "i",
              "output": {"artifacts": ["change"]}, "next": "done"},
}}
CHANGE = [ArtifactIn(name="change", path="src/x.py", commit=SHA, branch="task-1")]


@pytest.fixture
def svc(store, clock):
    return Service(store, parse_workflow(WORKFLOW), clock=clock)


def ops(svc, task_id, who=REQ):
    return [e.operation for e in svc.get_task(who, task_id).history if e.result == "ok"]


def route(svc, t, name="quick", reason="one-liner"):
    c = svc.claim_step(TRIAGE, t.step_id)
    return svc.complete_step(TRIAGE, t.step_id, c.claim_token, name, [], findings=reason)


def test_route_completion(svc):
    t = svc.create_task(REQ, "T")
    s = route(svc, t)
    assert s.current_step == "build"
    done = [e for e in svc.get_task(REQ, t.id).history if e.operation == "complete_step"][0]
    assert done.detail["route"] == "quick"


def test_unknown_route_and_missing_reason_refused(svc):
    t = svc.create_task(REQ, "T")
    c = svc.claim_step(TRIAGE, t.step_id)
    with pytest.raises(Rejected, match="outcome must be one of: quick, park"):
        svc.complete_step(TRIAGE, t.step_id, c.claim_token, "nope", [], findings="x")
    with pytest.raises(Rejected, match="reason"):
        svc.complete_step(TRIAGE, t.step_id, c.claim_token, "quick", [])
    with pytest.raises(Rejected, match="no artifacts"):
        svc.complete_step(TRIAGE, t.step_id, c.claim_token, "quick", CHANGE, findings="x")


def test_park_gate_route_and_cancel(svc):
    t = svc.create_task(REQ, "T")
    s = route(svc, t, "park", "later")
    assert s.current_step == "decide"
    with pytest.raises(Rejected, match="reason"):
        svc.decide_gate(APPROVER_REQ, t.id, None, "route", target="cancel")  # no reason
    with pytest.raises(Rejected, match="route must be one of: cancel"):
        svc.decide_gate(APPROVER_REQ, t.id, None, "route", reason="r", target="nope")
    s = svc.decide_gate(APPROVER_REQ, t.id, None, "route", reason="not now", target="cancel")
    assert (s.status, s.current_step, s.waiting_for) == ("cancelled", None, "cancelled")
    closed = [e for e in svc.get_task(REQ, t.id).history if e.operation == "task_closed"]
    assert [e.detail for e in closed] == [{"status": "cancelled"}]
    assert svc.list_tasks(REQ) == [] and len(svc.list_tasks(REQ, include_closed=True)) == 1


def test_gate_with_routes_refuses_approve(svc):
    t = svc.create_task(REQ, "T")
    route(svc, t, "park")
    for decision in ("approve", "reject"):
        with pytest.raises(Rejected, match="route"):
            svc.decide_gate(APPROVER_REQ, t.id, None, decision, reason="r")


def test_route_on_gate_without_routes_refused(svc, store, clock):
    from conftest import MVP
    mvp = Service(store, parse_workflow(MVP), clock=clock)
    t = mvp.create_task(REQ, "T")
    c = mvp.claim_step(Caller("p", frozenset({"planner"})), t.step_id)
    mvp.complete_step(Caller("p", frozenset({"planner"})), t.step_id, c.claim_token, "completed",
                      [ArtifactIn(name="design_doc", path="d.md", commit=SHA, branch="b")])
    with pytest.raises(Rejected, match="no routes"):
        mvp.decide_gate(APPROVER_REQ, t.id, None, "route", reason="r", target="x")


def test_step_opened_and_task_closed_events(svc):
    t = svc.create_task(REQ, "T")
    route(svc, t)
    c = svc.claim_step(BUILDER, svc.list_open_steps(BUILDER)[0].step_id)
    svc.complete_step(BUILDER, c.step_id, c.claim_token, "completed", CHANGE)
    assert ops(svc, t.id) == ["step_opened", "create_task", "claim_step", "step_opened",
                              "complete_step", "claim_step", "task_closed", "complete_step"]
    opened = [e for e in svc.get_task(REQ, t.id).history if e.operation == "step_opened"]
    assert [(e.step, e.detail) for e in opened] == [
        ("triage", {"step": "triage", "round": 1}), ("build", {"step": "build", "round": 1})]
    assert svc.get_task(REQ, t.id).history[0].subject == "core"


def test_list_events_feed(svc):
    t = svc.create_task(REQ, "T")
    feed = svc.list_events(ORCH, after_id=0)
    assert [e.operation for e in feed.events][:2] == ["step_opened", "create_task"]
    assert feed.newest_id == feed.events[-1].id
    assert (feed.events[0].task_id, feed.events[0].step) == (t.id, "triage")
    assert feed.events[0].values["task.title"] == "T"
    assert svc.list_events(ORCH, after_id=feed.newest_id).events == []
    assert svc.list_events(ORCH, after_id=feed.newest_id).newest_id == feed.newest_id
    assert len(svc.list_events(ORCH, after_id=0, limit=1).events) == 1
    with pytest.raises(Rejected):
        svc.list_events(REQ, after_id=0)
    with pytest.raises(Rejected):
        svc.claim_step(BUILDER, 99)  # rejected events stay out of the feed
    assert svc.list_events(ORCH, after_id=feed.newest_id).events == []


def test_values_include_task_and_evidence(svc):
    t = svc.create_task(REQ, "T", "do it")
    ctx = svc.get_step_context(TRIAGE, t.step_id)
    assert ctx.description == "Pick a route"
    assert ctx.values == {"task.id": str(t.id), "task.title": "T", "task.request": "do it"}
    s = route(svc, t)
    values = svc.get_step_context(BUILDER, s.step_id).values
    assert values["evidence.triage.findings"] == "one-liner"
    assert svc.get_task(REQ, t.id).description == ""


def test_route_at_intake(svc):
    t = svc.create_task(APPROVER_REQ, "T", route="quick")
    assert t.current_step == "build"
    detail = svc.get_task(REQ, t.id)
    assert ops(svc, t.id) == ["step_opened", "route_at_intake", "step_opened", "create_task"]
    e = [e for e in detail.history if e.operation == "route_at_intake"][0]
    assert e.detail == {"step": "triage", "route": "quick", "by": "boss"} and e.subject == "core"
    assert svc.get_step_context(BUILDER, t.step_id).values["evidence.triage.findings"] == \
        "route set at intake by boss"


def test_create_with_route_refusals(svc):
    with pytest.raises(Rejected, match="approver"):
        svc.create_task(REQ, "T", route="quick")
    with pytest.raises(Rejected, match="route"):
        svc.create_task(APPROVER_REQ, "T", route="nope")
    assert svc.list_tasks(APPROVER_REQ) == []


def test_route_at_intake_only_in_round_one(svc):
    t = svc.create_task(APPROVER_REQ, "T", route="park")
    assert t.current_step == "decide"
    s = svc.decide_gate(APPROVER_REQ, t.id, None, "route", reason="again", target="again")
    assert (s.current_step, svc.get_step_context(TRIAGE, s.step_id).round) == ("triage", 2)
    assert "claimed_by" not in s.waiting_for and "waiting for a triage" in s.waiting_for


def test_intake_run_is_claimed_by_the_subject(svc, store):
    t = svc.create_task(Caller("boss", APPROVER_REQ.roles, "cli"), "T", route="quick")
    with store.transaction() as tx:
        run = tx.list_step_runs(t.id)[0]
    assert (run.claimed_by, run.claimed_executor, run.outcome) == ("boss", None, "quick")


def test_values_drop_evidence_of_a_later_run_without_any(store, clock):
    wf = {"start": "build", "steps": {
        "build": {"kind": "agent", "role": "builder", "skill": "s", "instructions": "i",
                  "output": {"artifacts": ["change"]}, "next": "rev"},
        "rev": {"kind": "agent", "role": "reviewer", "skill": "s", "instructions": "i",
                "review_of": "build", "on_pass": "done", "on_issues": "build"}}}
    svc = Service(store, parse_workflow(wf), clock=clock)
    rev = Caller("r", frozenset({"reviewer", "builder"}))
    t = svc.create_task(REQ, "T")
    for outcome, findings in (("completed", None), ("issues", "X"), ("completed", None),
                              ("pass", None)):
        sid = svc.get_task(REQ, t.id).step_id
        c = svc.claim_step(rev, sid)
        svc.complete_step(rev, sid, c.claim_token, outcome, CHANGE if outcome == "completed" else [],
                          findings=findings)
        if outcome == "issues":
            assert svc.get_step_context(rev, svc.get_task(REQ, t.id).step_id).values[
                "evidence.rev.findings"] == "X"
    assert "evidence.rev.findings" not in svc.get_step_context(rev, sid).values


def test_list_events_limit_is_clamped(svc):
    svc.create_task(REQ, "T")
    assert len(svc.list_events(ORCH, after_id=0, limit=-1).events) == 1
    assert len(svc.list_events(ORCH, after_id=0, limit=0).events) == 1


def test_check_args_may_use_task_values(store, clock):
    wf = {"start": "v", "steps": {"v": {
        "kind": "agent", "role": "builder", "skill": "s", "instructions": "i", "evidence": ["r"],
        "check": {"mcp_server": "g", "tool": "t", "args": {"q": "{{task.title}}"},
                  "expect": {"ok": "yes"}}, "next": "done", "on_fail": "v"}}}
    seen = []
    svc = Service(store, parse_workflow(wf), clock=clock,
                  checker=lambda server, tool, args: seen.append(args) or {"ok": "yes"})
    t = svc.create_task(REQ, "Title")
    c = svc.claim_step(BUILDER, t.step_id)
    svc.complete_step(BUILDER, t.step_id, c.claim_token, "completed", [], evidence={"r": "1"})
    assert seen == [{"q": "Title"}]

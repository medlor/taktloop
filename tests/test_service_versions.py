import copy

from conftest import MVP, ME, SHA

from taktloop.models import ArtifactIn
from taktloop.service import Service
from taktloop.workflow import parse_workflow


def _changes(store):
    with store.transaction() as tx:
        return [e for e in tx.events_after(0, 100) if e.operation == "workflow_changed"]


def _versions(store, *wanted):
    with store.transaction() as tx:
        return [tx.get_workflow_version(v) for v in (wanted or (1, 2, 3)) if tx.get_workflow_version(v)]


def test_first_start_snapshots_file_version(store, clock):
    svc = Service(store, parse_workflow(MVP, 4), clock=clock)
    assert svc.live_version == 4
    assert [(v.version, v.source) for v in _versions(store, 4)] == [(4, "file")]
    assert _changes(store) == []


def test_same_workflow_no_new_version(store, clock):
    Service(store, parse_workflow(MVP), clock=clock)
    again = Service(store, parse_workflow(MVP), clock=clock)
    assert again.live_version == 1 and len(_versions(store)) == 1
    assert _changes(store) == []


def test_hand_edited_workflow_becomes_next_version(store, clock):
    Service(store, parse_workflow(MVP), clock=clock)
    edited = copy.deepcopy(MVP)
    edited["steps"]["plan"]["instructions"] = "Different."
    svc = Service(store, parse_workflow(edited), clock=clock)
    assert svc.live_version == 2 and len(_versions(store)) == 2
    (event,) = _changes(store)
    assert event.detail == {"version": 2, "source": "file"} and event.task_id is None
    assert event.subject == "core"
    assert svc.live.data["steps"]["plan"]["instructions"] == "Different."


def test_task_keeps_its_version(store, clock):
    v1 = Service(store, parse_workflow(MVP), clock=clock)
    task = v1.create_task(ME, "t")
    renamed = copy.deepcopy(MVP)
    steps = renamed["steps"]
    renamed["steps"] = {("planning" if k == "plan" else k): v for k, v in steps.items()}
    renamed["steps"]["design_approval"]["on_approve"] = "planning"
    v2 = Service(store, parse_workflow(renamed), clock=clock)
    assert v2.live_version == 2
    old = v2.get_task(ME, task.id)
    assert old.current_step == "design"
    run = v2.claim_step(ME, old.step_id)
    v2.complete_step(ME, old.step_id, run.claim_token, "completed",
                     [ArtifactIn(name="design_doc", path="d.md", commit=SHA, branch="b")])
    v2.decide_gate(ME, task.id, "design_approval", "approve")
    assert v2.get_task(ME, task.id).current_step == "plan"
    new = v2.create_task(ME, "u")
    run = v2.claim_step(ME, new.step_id)
    v2.complete_step(ME, new.step_id, run.claim_token, "completed",
                     [ArtifactIn(name="design_doc", path="d.md", commit=SHA, branch="b")])
    v2.decide_gate(ME, new.id, "design_approval", "approve")
    assert v2.get_task(ME, new.id).current_step == "planning"
    with store.transaction() as tx:
        assert tx.get_task(new.id).workflow_version == 2
        assert tx.get_task(task.id).workflow_version == 1

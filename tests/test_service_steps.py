import threading
from datetime import datetime, timedelta, timezone

import pytest
import yaml

from taktloop.models import ArtifactIn
from taktloop.service import Caller, Rejected, Service
from taktloop.store import Store
from taktloop.workflow import parse_workflow

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
ME = Caller("local", frozenset({"*"}), "claude-code")
SHA = "a" * 40


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 1, 14, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


@pytest.fixture
def svc(store, clock):
    return Service(store, parse_workflow(MVP), clock=clock)


def art(path="docs/d.md", commit=SHA, branch="main"):
    return ArtifactIn(name="design_doc", path=path, commit=commit, branch=branch)


def claimed(svc):
    t = svc.create_task(ME, "T", "do it")
    return t, svc.claim_step(ME, t.step_id)


def events(store, task_id=1):
    with store.transaction() as tx:
        return tx.list_events(task_id)


def test_create_task_opens_start_step(svc):
    t = svc.create_task(ME, "T", "r")
    assert t.current_step == "design"
    assert t.waiting_for == "waiting for a planner to pick up design"
    [o] = svc.list_open_steps(ME)
    assert o.skill == "brainstorming" and o.step_id == t.step_id and o.round == 1


def test_create_task_rejects_empty_title(svc):
    with pytest.raises(Rejected):
        svc.create_task(ME, "  ")


def test_claim_returns_token_and_lease(svc, clock):
    t = svc.create_task(ME, "T")
    c = svc.claim_step(ME, t.step_id)
    assert c.lease_expires_at == clock() + timedelta(minutes=30)
    assert c.claim_token
    assert svc.list_open_steps(ME) == []


def test_second_claim_rejected(svc):
    t, _ = claimed(svc)
    with pytest.raises(Rejected) as e:
        svc.claim_step(ME, t.step_id)
    assert e.value.reason == "step is already claimed"


def test_unknown_step_rejected_and_recorded(svc, store):
    t = svc.create_task(ME, "T")
    with pytest.raises(Rejected, match="unknown step 99"):
        svc.claim_step(ME, 99)
    with store.transaction() as tx:
        row = tx._c.execute("SELECT * FROM events ORDER BY id DESC LIMIT 1").fetchone()
    assert row["result"] == "rejected" and row["task_id"] is None


def test_wrong_token_rejected_for_heartbeat_complete_release(svc):
    t, _ = claimed(svc)
    with pytest.raises(Rejected):
        svc.heartbeat(ME, t.step_id, "nope")
    with pytest.raises(Rejected):
        svc.complete_step(ME, t.step_id, "nope", "completed", [art()])
    with pytest.raises(Rejected):
        svc.release_step(ME, t.step_id, "why", claim_token="nope")


def test_heartbeat_extends_lease(svc, clock):
    t, c = claimed(svc)
    clock.now += timedelta(minutes=20)
    h = svc.heartbeat(ME, t.step_id, c.claim_token)
    assert h.lease_expires_at == clock() + timedelta(minutes=30)
    assert h.claim_token == c.claim_token


def test_complete_requires_all_artifacts(svc):
    t, c = claimed(svc)
    with pytest.raises(Rejected, match="design_doc"):
        svc.complete_step(ME, t.step_id, c.claim_token, "completed", [])


@pytest.mark.parametrize("bad", [
    {"path": "../x.md"}, {"path": "/etc/passwd"}, {"path": "a/../../b"}, {"path": " "},
    {"commit": "abc123"}, {"commit": "Z" * 40}, {"branch": ""},
])
def test_complete_rejects_bad_paths_and_shas(svc, store, bad):
    t, c = claimed(svc)
    with store.transaction() as tx:
        before = tx.get_step_run(t.step_id)
    with pytest.raises(Rejected, match="design_doc"):
        svc.complete_step(ME, t.step_id, c.claim_token, "completed", [art(**bad)])
    with store.transaction() as tx:
        assert tx.get_step_run(t.step_id) == before
        assert tx.list_artifacts(t.id) == []
    assert events(store)[-1].result == "rejected"


def test_complete_rejects_other_outcome(svc):
    t, c = claimed(svc)
    with pytest.raises(Rejected):
        svc.complete_step(ME, t.step_id, c.claim_token, "issues", [art()])


def test_complete_opens_next_step(svc, store):
    t, c = claimed(svc)
    s = svc.complete_step(ME, t.step_id, c.claim_token, "completed", [art()])
    assert s.current_step == "design_approval"
    assert s.waiting_for == "waiting for approver decision at design_approval"
    assert svc.list_open_steps(ME) == []
    with store.transaction() as tx:
        assert tx.get_step_run(t.step_id).status == "completed"
        assert [a.name for a in tx.list_artifacts(t.id)] == ["design_doc"]
    ev = events(store)[-1]
    assert ev.operation == "complete_step" and ev.result == "ok"
    assert "claim_token" not in ev.detail and ev.caller == "local (claude-code)"


def test_gate_run_cannot_be_claimed(svc):
    t, c = claimed(svc)
    s = svc.complete_step(ME, t.step_id, c.claim_token, "completed", [art()])
    with pytest.raises(Rejected, match="human gate"):
        svc.claim_step(ME, s.step_id)


def test_completed_step_cannot_be_claimed(svc):
    t, c = claimed(svc)
    svc.complete_step(ME, t.step_id, c.claim_token, "completed", [art()])
    with pytest.raises(Rejected, match="not open"):
        svc.claim_step(ME, t.step_id)


def test_waiting_for_when_claimed(svc):
    t, _ = claimed(svc)
    with svc._store.transaction() as tx:
        s = svc._summary(tx, tx.get_task(t.id))
    assert s.waiting_for == "design in progress by local (claude-code), lease until 14:30 UTC"


def test_lease_expires_lazily(svc, store, clock):
    t, c = claimed(svc)
    clock.now += timedelta(minutes=31)
    assert [o.step_id for o in svc.list_open_steps(ME)] == [t.step_id]
    svc.list_open_steps(ME)
    with pytest.raises(Rejected, match="lease expired"):
        svc.complete_step(ME, t.step_id, c.claim_token, "completed", [art()])
    assert [e.operation for e in events(store)].count("lease_expired") == 1
    c2 = svc.claim_step(ME, t.step_id)
    assert c2.claim_token != c.claim_token
    svc.complete_step(ME, t.step_id, c2.claim_token, "completed", [art()])


def test_release_returns_step_and_force_needs_no_token(svc):
    t, c = claimed(svc)
    s = svc.release_step(ME, t.step_id, "stuck", claim_token=c.claim_token)
    assert s.step_id == t.step_id and s.waiting_for.startswith("waiting for a planner")
    with pytest.raises(Rejected, match="not claimed"):
        svc.release_step(ME, t.step_id, "again", force=True)
    svc.claim_step(ME, t.step_id)
    with pytest.raises(Rejected):
        svc.release_step(ME, t.step_id, "no token")
    with pytest.raises(Rejected, match="reason"):
        svc.release_step(ME, t.step_id, " ", force=True)
    assert svc.release_step(ME, t.step_id, "human freed it", force=True).current_step == "design"
    assert [o.step_id for o in svc.list_open_steps(ME)] == [t.step_id]


def test_design_step_context(svc):
    t, _ = claimed(svc)
    ctx = svc.get_step_context(ME, t.step_id)
    assert ctx.request == "do it" and ctx.skill == "brainstorming"
    assert ctx.instructions.startswith("Run brainstorming")
    assert ctx.required_artifacts == ["design_doc"]
    assert ctx.round == 1 and ctx.rejection_reason is None and ctx.artifacts == []


def test_concurrent_claims_one_winner(tmp_path):
    store = Store(tmp_path / "f.db")
    svc = Service(store, parse_workflow(MVP))
    t = svc.create_task(ME, "T")
    results, errors = [], []

    def go():
        try:
            results.append(svc.claim_step(ME, t.step_id))
        except Exception as e:  # noqa: BLE001 - collected and asserted below
            errors.append(e)

    threads = [threading.Thread(target=go) for _ in range(8)]
    [th.start() for th in threads]
    [th.join() for th in threads]
    store.close()
    assert len(results) == 1
    assert len(errors) == 7 and all(isinstance(e, Rejected) for e in errors)

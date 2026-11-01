import sqlite3
import threading
from datetime import UTC, datetime, timedelta

import pytest

from taktloop.store import ArtifactRow, SchemaMismatch, Store

T0 = datetime(2026, 11, 1, 12, 0, tzinfo=UTC)


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


def test_task_and_step_run_roundtrip(store):
    with store.transaction() as tx:
        tid = tx.insert_task("T", "do it", "alice", T0)
        sid = tx.insert_step_run(tid, "design", 1, T0)
        task = tx.get_task(tid)
        run = tx.get_step_run(sid)
        assert (task.title, task.request, task.status, task.created_by, task.created_at) == (
            "T", "do it", "active", "alice", T0)
        assert task.created_at.tzinfo is not None
        assert (run.task_id, run.step, run.round, run.status) == (tid, "design", 1, "open")
        assert run.opened_at == T0
        assert run.claimed_by is None and run.lease_expires_at is None and run.evidence is None
        assert tx.current_step_run(tid) == run
        assert tx.last_step_run(tid, "design") == run
        assert tx.last_step_run(tid, "other") is None
        assert tx.get_task(999) is None and tx.get_step_run(999) is None
        tx.update_step_run(sid, status="completed")
        assert tx.current_step_run(tid) is None
        assert tx.last_step_run(tid, "design").status == "completed"


def test_update_step_run_fields(store):
    exp = T0 + timedelta(minutes=30)
    with store.transaction() as tx:
        tid = tx.insert_task("T", "r", "a", T0)
        sid = tx.insert_step_run(tid, "s", 1, T0)
        tx.update_step_run(sid, status="claimed", claimed_by="bob", claim_token="tok",
                           lease_expires_at=exp, evidence={"k": [1]}, closed_at=T0)
        run = tx.get_step_run(sid)
        assert (run.claimed_by, run.claim_token, run.lease_expires_at) == ("bob", "tok", exp)
        assert run.evidence == {"k": [1]} and run.closed_at == T0
        assert tx.step_runs_with_status(["claimed"]) == [run]
        assert tx.step_runs_with_status(["open"]) == []
        tx.update_step_run(sid, lease_expires_at=None, evidence=None)
        run = tx.get_step_run(sid)
        assert run.lease_expires_at is None and run.evidence is None
        with pytest.raises(ValueError):
            tx.update_step_run(sid, bogus=1)
        with pytest.raises(ValueError):
            tx.update_step_run(sid, id=5)


def test_tasks_and_artifacts(store):
    with store.transaction() as tx:
        a = tx.insert_task("A", "r", "x", T0)
        b = tx.insert_task("B", "r", "x", T0)
        tx.set_task_status(b, "done")
        assert [t.id for t in tx.list_tasks(["active"])] == [a]
        assert [t.id for t in tx.list_tasks(["active", "done"])] == [a, b]
        assert tx.list_tasks([]) == []
        sid = tx.insert_step_run(a, "s", 1, T0)
        art = ArtifactRow(a, sid, "design_doc", "docs/d.md", "a" * 40, "main")
        tx.insert_artifact(art)
        assert tx.list_artifacts(a) == [art]
        assert tx.list_artifacts(b) == []


def test_rollback_on_exception(store):
    with pytest.raises(RuntimeError):
        with store.transaction() as tx:
            tid = tx.insert_task("T", "r", "a", T0)
            raise RuntimeError("boom")
    with store.transaction() as tx:
        assert tx.get_task(tid) is None


def test_persists_across_instances(tmp_path):
    path = tmp_path / "sub" / "f.db"
    s = Store(path)
    with s.transaction() as tx:
        tid = tx.insert_task("T", "r", "a", T0)
    s.close()
    s = Store(path)
    with s.transaction() as tx:
        assert tx.get_task(tid).title == "T"
    s.close()


def test_schema_version_mismatch(tmp_path):
    path = tmp_path / "f.db"
    Store(path).close()
    raw = sqlite3.connect(path)
    raw.execute("PRAGMA user_version = 99")
    raw.close()
    with pytest.raises(SchemaMismatch):
        Store(path)


def test_events_ordered_and_detail_json(store):
    with store.transaction() as tx:
        e1 = tx.insert_event(T0, "alice", "claim", 7, None, {"a": [1]}, "ok", None)
        e2 = tx.insert_event(T0, "bob", "complete", 7, 3, {}, "rejected", "nope")
        tx.insert_event(T0, "bob", "claim", 8, None, {}, "ok", None)
        evs = tx.list_events(7)
    assert [e.id for e in evs] == [e1, e2]
    assert evs[0].detail == {"a": [1]} and evs[0].reason is None and evs[0].at == T0
    assert (evs[1].result, evs[1].reason, evs[1].step_run_id) == ("rejected", "nope", 3)


def test_concurrent_transactions(tmp_path):
    s = Store(tmp_path / "f.db")
    with s.transaction() as tx:
        tid = tx.insert_task("T", "r", "a", T0)
        sid = tx.insert_step_run(tid, "s", 1, T0)
    wins = []

    def claim(n):
        with s.transaction() as tx:
            if tx.get_step_run(sid).status == "open":
                tx.update_step_run(sid, status="claimed", claimed_by=str(n))
                wins.append(n)

    threads = [threading.Thread(target=claim, args=(n,)) for n in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    s.close()
    assert len(wins) == 1

"""SQLite store: schema, transactions and plain-SQL row access."""

from __future__ import annotations

import dataclasses
import json
import sqlite3
import threading
from collections.abc import Collection, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 2

_SCHEMA = """
CREATE TABLE tasks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL, request TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active',
    created_by TEXT NOT NULL, created_at TEXT NOT NULL
);
CREATE TABLE step_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    step TEXT NOT NULL, round INTEGER NOT NULL,
    status TEXT NOT NULL DEFAULT 'open',
    claimed_by TEXT, claimed_executor TEXT, claim_token TEXT, lease_expires_at TEXT,
    outcome TEXT, evidence TEXT,
    opened_at TEXT NOT NULL, closed_at TEXT
);
CREATE TABLE artifacts (
    task_id INTEGER NOT NULL REFERENCES tasks(id),
    step_run_id INTEGER NOT NULL REFERENCES step_runs(id),
    name TEXT NOT NULL, path TEXT NOT NULL, "commit" TEXT NOT NULL, branch TEXT NOT NULL
);
CREATE TABLE events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    at TEXT NOT NULL, subject TEXT NOT NULL, roles TEXT NOT NULL, executor TEXT,
    operation TEXT NOT NULL,
    task_id INTEGER, step_run_id INTEGER,
    detail TEXT NOT NULL, result TEXT NOT NULL, reason TEXT
);
CREATE INDEX step_runs_task ON step_runs(task_id);
CREATE INDEX artifacts_task ON artifacts(task_id);
CREATE INDEX events_task ON events(task_id);
"""


class SchemaMismatch(RuntimeError):
    pass


@dataclass(frozen=True)
class TaskRow:
    id: int
    title: str
    request: str
    status: str
    created_by: str
    created_at: datetime


@dataclass(frozen=True)
class StepRunRow:
    id: int
    task_id: int
    step: str
    round: int
    status: str
    claimed_by: str | None
    claimed_executor: str | None
    claim_token: str | None
    lease_expires_at: datetime | None
    outcome: str | None
    evidence: dict | None
    opened_at: datetime
    closed_at: datetime | None


@dataclass(frozen=True)
class ArtifactRow:
    task_id: int
    step_run_id: int
    name: str
    path: str
    commit: str
    branch: str


@dataclass(frozen=True)
class EventRow:
    id: int
    at: datetime
    subject: str
    roles: list[str]
    executor: str | None
    operation: str
    task_id: int | None
    step_run_id: int | None
    detail: dict
    result: str
    reason: str | None


_DATETIME_FIELDS = {"created_at", "lease_expires_at", "opened_at", "closed_at", "at"}
_JSON_FIELDS = {"evidence", "detail", "roles"}
_STEP_RUN_FIELDS = {f.name for f in dataclasses.fields(StepRunRow)} - {"id"}


def _encode(name: str, value: Any) -> Any:
    if value is None:
        return None
    if name in _DATETIME_FIELDS:
        return value.isoformat()
    if name in _JSON_FIELDS:
        return json.dumps(value)
    return value


def _row(cls: type, r: sqlite3.Row | None) -> Any:
    if r is None:
        return None
    values = {}
    for f in dataclasses.fields(cls):
        v = r[f.name]
        if v is not None and f.name in _DATETIME_FIELDS:
            v = datetime.fromisoformat(v)
        elif v is not None and f.name in _JSON_FIELDS:
            v = json.loads(v)
        values[f.name] = v
    return cls(**values)


def _marks(items: Collection[str]) -> str:
    return ",".join("?" * len(items))


class Tx:
    """Row operations inside one transaction; obtain via Store.transaction()."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self._c = conn

    def _one(self, cls: type, sql: str, args: tuple = ()) -> Any:
        return _row(cls, self._c.execute(sql, args).fetchone())

    def _all(self, cls: type, sql: str, args: tuple = ()) -> list:
        return [_row(cls, r) for r in self._c.execute(sql, args).fetchall()]

    # tasks
    def insert_task(self, title: str, request: str, created_by: str, at: datetime) -> int:
        cur = self._c.execute(
            "INSERT INTO tasks (title, request, created_by, created_at) VALUES (?,?,?,?)",
            (title, request, created_by, _encode("created_at", at)))
        return cur.lastrowid

    def get_task(self, id: int) -> TaskRow | None:
        return self._one(TaskRow, "SELECT * FROM tasks WHERE id=?", (id,))

    def list_tasks(self, statuses: Collection[str]) -> list[TaskRow]:
        statuses = list(statuses)
        return self._all(
            TaskRow, f"SELECT * FROM tasks WHERE status IN ({_marks(statuses)}) ORDER BY id",
            tuple(statuses))

    def set_task_status(self, id: int, status: str) -> None:
        self._c.execute("UPDATE tasks SET status=? WHERE id=?", (status, id))

    # step runs
    def insert_step_run(self, task_id: int, step: str, round: int, at: datetime) -> int:
        cur = self._c.execute(
            "INSERT INTO step_runs (task_id, step, round, opened_at) VALUES (?,?,?,?)",
            (task_id, step, round, _encode("opened_at", at)))
        return cur.lastrowid

    def get_step_run(self, id: int) -> StepRunRow | None:
        return self._one(StepRunRow, "SELECT * FROM step_runs WHERE id=?", (id,))

    def current_step_run(self, task_id: int) -> StepRunRow | None:
        return self._one(
            StepRunRow,
            "SELECT * FROM step_runs WHERE task_id=? AND status IN ('open','claimed') "
            "ORDER BY id DESC LIMIT 1", (task_id,))

    def last_step_run(self, task_id: int, step: str) -> StepRunRow | None:
        return self._one(
            StepRunRow,
            "SELECT * FROM step_runs WHERE task_id=? AND step=? ORDER BY id DESC LIMIT 1",
            (task_id, step))

    def step_runs_with_status(self, statuses: Collection[str]) -> list[StepRunRow]:
        statuses = list(statuses)
        return self._all(
            StepRunRow,
            f"SELECT * FROM step_runs WHERE status IN ({_marks(statuses)}) ORDER BY id",
            tuple(statuses))

    def update_step_run(self, id: int, /, **fields: Any) -> None:
        unknown = set(fields) - _STEP_RUN_FIELDS
        if unknown:
            raise ValueError(f"unknown step_run fields: {sorted(unknown)}")
        if not fields:
            return
        sets = ", ".join(f"{k}=?" for k in fields)
        self._c.execute(f"UPDATE step_runs SET {sets} WHERE id=?",
                        (*(_encode(k, v) for k, v in fields.items()), id))

    # artifacts
    def insert_artifact(self, row: ArtifactRow) -> None:
        self._c.execute(
            'INSERT INTO artifacts (task_id, step_run_id, name, path, "commit", branch) '
            "VALUES (?,?,?,?,?,?)",
            (row.task_id, row.step_run_id, row.name, row.path, row.commit, row.branch))

    def list_artifacts(self, task_id: int) -> list[ArtifactRow]:
        return self._all(ArtifactRow, "SELECT * FROM artifacts WHERE task_id=? ORDER BY rowid",
                         (task_id,))

    # events
    def insert_event(self, at: datetime, subject: str, roles: list[str], executor: str | None,
                     operation: str, task_id: int | None, step_run_id: int | None,
                     detail: dict, result: str, reason: str | None) -> int:
        cur = self._c.execute(
            "INSERT INTO events (at, subject, roles, executor, operation, task_id, step_run_id, "
            "detail, result, reason) VALUES (?,?,?,?,?,?,?,?,?,?)",
            (_encode("at", at), subject, _encode("roles", roles), executor, operation, task_id,
             step_run_id, _encode("detail", detail), result, reason))
        return cur.lastrowid

    def list_events(self, task_id: int) -> list[EventRow]:
        return self._all(EventRow, "SELECT * FROM events WHERE task_id=? ORDER BY id", (task_id,))


class Store:
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.execute("PRAGMA journal_mode = WAL")
            version = self._conn.execute("PRAGMA user_version").fetchone()[0]
            if version == 0:
                with self._lock:
                    self._conn.executescript(
                        f"BEGIN IMMEDIATE;{_SCHEMA}PRAGMA user_version = {SCHEMA_VERSION};COMMIT;")
            elif version != SCHEMA_VERSION:
                raise SchemaMismatch(
                    f"{path} has schema version {version}, expected {SCHEMA_VERSION}; delete "
                    f"{path} to start fresh (M1 data is not migrated)")
        except BaseException:
            self._conn.close()
            raise

    @contextmanager
    def transaction(self) -> Iterator[Tx]:
        with self._lock:
            self._conn.execute("BEGIN IMMEDIATE")
            try:
                yield Tx(self._conn)
            except BaseException:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def close(self) -> None:
        self._conn.close()

"""Row types of the SQLite store."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class TaskRow:
    id: int
    title: str
    request: str
    status: str
    created_by: str
    created_at: datetime
    route: str | None
    workflow_version: int


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
class WorkflowVersionRow:
    version: int
    data: dict
    sha256: str
    source: str
    approved_by: str | None
    approved_at: datetime


@dataclass(frozen=True)
class DraftRow:
    base_version: int
    data: dict
    updated_by: str
    updated_at: datetime


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

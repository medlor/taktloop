"""Pydantic result/input models shared by service, server and CLI."""

from datetime import datetime

from pydantic import BaseModel


class ArtifactIn(BaseModel):
    name: str
    path: str
    commit: str
    branch: str


class Artifact(ArtifactIn):
    step: str
    round: int


class TaskSummary(BaseModel):
    id: int
    title: str
    status: str
    current_step: str | None
    step_id: int | None
    waiting_for: str


class OpenStep(BaseModel):
    step_id: int
    task_id: int
    task_title: str
    step: str
    skill: str
    round: int


class Claim(BaseModel):
    step_id: int
    claim_token: str
    lease_expires_at: datetime


class StepContext(BaseModel):
    step_id: int
    task_id: int
    task_title: str
    request: str
    step: str
    skill: str
    instructions: str
    required_artifacts: list[str]
    round: int
    rejection_reason: str | None
    artifacts: list[Artifact]


class Event(BaseModel):
    at: datetime
    caller: str
    operation: str
    step: str | None
    result: str
    reason: str | None
    detail: dict


class TaskDetail(TaskSummary):
    request: str
    artifacts: list[Artifact]
    history: list[Event]

"""Pydantic result/input models shared by service, server and CLI."""

from datetime import datetime
from typing import Literal

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


class Feedback(BaseModel):
    """What reopened a step's current round: a gate rejection or a review's findings."""
    kind: Literal["gate_rejection", "review_issues"]
    text: str
    by: str
    at: datetime


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
    feedback: Feedback | None
    artifacts: list[Artifact]


class Event(BaseModel):
    at: datetime
    subject: str
    roles: list[str]
    executor: str | None
    operation: str
    step: str | None
    result: str
    reason: str | None
    detail: dict


class TaskDetail(TaskSummary):
    request: str
    artifacts: list[Artifact]
    history: list[Event]


class TaskList(BaseModel):
    tasks: list[TaskSummary]


class OpenStepList(BaseModel):
    steps: list[OpenStep]

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


class SweptStep(BaseModel):
    """An open or claimed step run as the orchestrator sees it."""
    step_id: int
    task_id: int
    step: str
    kind: str
    role: str
    dispatch: str
    push_identity: str | None
    status: Literal["open", "claimed"]
    claimed_by: str | None
    idle_since: datetime
    lease_expiries: int
    stalled: bool


class SweptStepList(BaseModel):
    steps: list[SweptStep]


class Claim(BaseModel):
    step_id: int
    claim_token: str
    lease_expires_at: datetime


class Feedback(BaseModel):
    """What reopened a step's current round: a gate rejection, review findings, a red check."""
    kind: Literal["gate_rejection", "review_issues", "check_failed"]
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
    review_of: str | None = None  # set on review steps: the step whose work is reviewed
    description: str = ""
    values: dict[str, str] = {}  # task.* and evidence.<step>.<key>, for {{...}} in hooks and checks


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


class FeedEvent(BaseModel):
    id: int
    at: datetime
    subject: str
    operation: str
    task_id: int | None
    step: str | None
    detail: dict
    values: dict[str, str]


class EventFeed(BaseModel):
    events: list[FeedEvent]
    newest_id: int


class TaskDetail(TaskSummary):
    request: str
    description: str = ""  # of the current step
    artifacts: list[Artifact]
    history: list[Event]


class TaskList(BaseModel):
    tasks: list[TaskSummary]


class OpenStepList(BaseModel):
    steps: list[OpenStep]

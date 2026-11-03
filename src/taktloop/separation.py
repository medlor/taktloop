"""Strict separation of duties (spec 6.4): fixed rules as pure functions over the core's
records of who completed which agent run of a task. Not configurable beyond on/off."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from taktloop.workflow import Step, Workflow


@dataclass(frozen=True)
class Record:
    """One completed agent run of a task."""
    step: str
    round: int
    by: str
    task: int


def approved_step(wf: Workflow, gate: Step | None, review: Step | None) -> str:
    """The step whose artifact a gate decision or an override approves."""
    review = review or wf.escalating_review(gate.name)
    if review is not None:
        return review.review_of
    return gate.approves or ""


def _refuse(steps: Iterable[str], subject: str, records: list[Record]) -> str | None:
    steps = set(steps)
    r = next((r for r in records if r.by == subject and r.step in steps), None)
    return r and f"strict separation: {r.by} completed {r.step} round {r.round} of task {r.task}"


def check_claim(wf: Workflow, step: Step, subject: str, records: list[Record]) -> str | None:
    """Rule 1: no reviewing a step you worked on. Rule 2: no working on a step you reviewed."""
    if step.is_review:
        return _refuse([step.review_of], subject, records)
    return _refuse((s.name for s in wf.steps.values() if s.review_of == step.name),
                   subject, records)


def check_approval(approved: str, subject: str, records: list[Record]) -> str | None:
    """Rule 3: no approving a step you worked on."""
    return _refuse([approved], subject, records)

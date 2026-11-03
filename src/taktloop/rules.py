"""Pure, state-free rules for steps: validation, token check, waiting-for text."""

from __future__ import annotations

import re
import secrets
from collections.abc import Iterable
from datetime import datetime, timezone
from pathlib import PurePosixPath

from taktloop.models import Artifact, ArtifactIn
from taktloop.workflow import Step

_SHA = re.compile(r"[0-9a-f]{40}")


class Rejected(Exception):
    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def same_token(a: str, b: str) -> bool:
    return secrets.compare_digest(a.encode(), b.encode())


def check_artifacts(required: Iterable[str], artifacts: list[ArtifactIn]) -> None:
    """Raise Rejected unless every required name is present and every artifact is well-formed."""
    have = {a.name for a in artifacts}
    missing = [n for n in required if n not in have]
    if missing:
        raise Rejected(f"missing required artifacts: {', '.join(missing)}")
    for a in artifacts:
        p = PurePosixPath(a.path)
        if not a.path.strip() or p.is_absolute() or ".." in p.parts:
            raise Rejected(
                f"artifact {a.name!r}: path must be non-empty, relative and without '..'")
        if not _SHA.fullmatch(a.commit):
            raise Rejected(f"artifact {a.name!r}: commit must be a 40-character lowercase hex SHA")
        if not a.branch.strip():
            raise Rejected(f"artifact {a.name!r}: branch must not be empty")


def waiting_for(step: Step | None, status: str = "open", claimed_by: str | None = None,
                lease_expires_at: datetime | None = None, escalated: Step | None = None,
                stalled: str | None = None) -> str:
    """Spec 6.6 text for the current step run; step None means the task is done.
    `escalated` is the review step whose escalation gate `step` is; `stalled` the reason the
    orchestrator reported the run stalled."""
    if step is None:
        return "done"
    if status == "claimed":
        until = lease_expires_at.astimezone(timezone.utc).strftime("%H:%M")
        text = f"{step.name} in progress by {claimed_by}, lease until {until} UTC"
    elif step.kind == "human_gate":
        why = (f" ({escalated.review_of} failed review {escalated.max_issue_rounds} times)"
               if escalated else "")
        text = f"waiting for {step.role} decision at {step.name}{why}"
    elif step.kind == "code":
        text = f"waiting for the orchestrator to run {step.name}"
    else:
        text = f"waiting for a {step.role} to pick up {step.name}"
    return f"{text} — stalled: {stalled}" if stalled else text


def latest_rounds(artifacts: Iterable[Artifact], exclude_step: str | None = None) -> list[Artifact]:
    """Artifacts of each step's latest round that has any (artifacts exist only once completed)."""
    arts = [a for a in artifacts if a.step != exclude_step]
    newest: dict[str, int] = {}
    for a in arts:
        newest[a.step] = max(newest.get(a.step, 0), a.round)
    return [a for a in arts if a.round == newest[a.step]]


def clean_reason(reason: str | None) -> str:
    """The stripped reason, or Rejected when absent or blank."""
    if not reason or not reason.strip():
        raise Rejected("reason is required")
    return reason.strip()

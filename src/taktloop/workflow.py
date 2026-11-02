"""Parse and validate the ``workflow:`` block of factory.yaml. Pure, no I/O."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

DONE = "done"  # reserved target name: the task is finished
KINDS = ("agent", "human_gate")
REVIEW_KEYS = ("review_of", "on_pass", "on_issues", "max_issue_rounds", "on_escalate")


class WorkflowError(ValueError):
    """The workflow definition is invalid."""


@dataclass(frozen=True)
class Step:
    name: str
    kind: Literal["agent", "human_gate"]
    role: str
    skill: str | None
    instructions: str
    required_artifacts: tuple[str, ...]
    next: str | None
    on_approve: str | None
    on_reject: tuple[str, ...]
    review_of: str | None = None
    on_pass: str | None = None
    on_issues: str | None = None
    max_issue_rounds: int | None = None
    on_escalate: str | None = None

    @property
    def is_review(self) -> bool:
        return self.review_of is not None

    @property
    def targets(self) -> tuple[str, ...]:
        named = (self.next, self.on_approve, *self.on_reject, self.review_of, self.on_pass,
                 self.on_issues, self.on_escalate)
        return tuple(t for t in named if t)


@dataclass(frozen=True)
class Workflow:
    start: str
    steps: Mapping[str, Step]

    def step(self, name: str) -> Step:
        try:
            return self.steps[name]
        except KeyError:
            raise WorkflowError(f"unknown step {name!r}") from None

    def escalating_review(self, gate: str) -> Step | None:
        """The review step whose on_escalate is this gate, if any."""
        return next((s for s in self.steps.values() if s.on_escalate == gate), None)


def _parse_step(name: str, raw: Any) -> Step:
    if not isinstance(raw, dict):
        raise WorkflowError(f"step {name!r}: definition must be a mapping")
    if name == DONE:
        raise WorkflowError(f"step name {DONE!r} is reserved")
    role = raw.get("role")
    if not role:
        raise WorkflowError(f"step {name!r}: role is required")
    kind = raw.get("kind")
    if kind not in KINDS:
        raise WorkflowError(
            f"step {name!r}: kind {kind!r} is not supported yet (supported: {', '.join(KINDS)})"
        )
    skill, artifacts, nxt, approve, reject = None, (), None, None, ()
    review = {k: raw.get(k) for k in REVIEW_KEYS}
    if kind == "agent":
        skill = raw.get("skill")
        artifacts = tuple((raw.get("output") or {}).get("artifacts") or ())
        nxt = raw.get("next")
        if not skill:
            raise WorkflowError(f"step {name!r}: agent steps need a skill")
        if any(v is not None for v in review.values()):
            _check_review(name, review, raw)
        elif not artifacts:
            raise WorkflowError(f"step {name!r}: agent steps need output.artifacts")
        elif not nxt:
            raise WorkflowError(f"step {name!r}: agent steps need next")
    else:
        approve, reject = raw.get("on_approve"), raw.get("on_reject")
        reject = (reject,) if isinstance(reject, str) else tuple(reject or ())
        if not approve or not reject:
            raise WorkflowError(f"step {name!r}: gates need on_approve and on_reject")
        review = {}
    return Step(
        name=name,
        kind=kind,
        role=str(role),
        skill=skill,
        instructions=" ".join(str(raw.get("instructions") or "").split()),
        required_artifacts=artifacts,
        next=nxt,
        on_approve=approve,
        on_reject=reject,
        **review,
    )


def _check_review(name: str, review: dict, raw: dict) -> None:
    if not (review["review_of"] and review["on_pass"] and review["on_issues"]):
        raise WorkflowError(f"step {name!r}: review steps need review_of, on_pass and on_issues")
    if (review["max_issue_rounds"] is None) != (review["on_escalate"] is None):
        raise WorkflowError(f"step {name!r}: max_issue_rounds and on_escalate go together")
    rounds = review["max_issue_rounds"]
    if rounds is not None and (type(rounds) is not int or rounds < 1):
        raise WorkflowError(f"step {name!r}: max_issue_rounds must be a positive integer")
    if "output" in raw or "next" in raw:
        raise WorkflowError(f"step {name!r}: review steps have no output or next")


def parse_workflow(data: dict) -> Workflow:
    raw_steps = data.get("steps")
    if not isinstance(raw_steps, dict) or not raw_steps:
        raise WorkflowError("workflow needs a non-empty 'steps' mapping")
    steps = {str(n): _parse_step(str(n), raw) for n, raw in raw_steps.items()}
    for step in steps.values():
        work = steps.get(step.review_of)
        if step.is_review and (work is None or work.kind != "agent" or work.is_review):
            raise WorkflowError(f"step {step.name!r}: review_of must name an agent work step")
    start = data.get("start")
    if start not in steps:
        raise WorkflowError(f"start step {start!r} does not exist")
    for step in steps.values():
        for target in step.targets:
            if target != DONE and target not in steps:
                raise WorkflowError(f"step {step.name!r} points to unknown step {target!r}")
    seen, todo = set(), [start]
    while todo:
        name = todo.pop()
        if name in seen or name == DONE:
            continue
        seen.add(name)
        todo.extend(steps[name].targets)
    unreachable = sorted(set(steps) - seen)
    if unreachable:
        raise WorkflowError(f"unreachable steps: {', '.join(unreachable)}")
    return Workflow(start=start, steps=steps)

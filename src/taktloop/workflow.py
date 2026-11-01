"""Parse and validate the ``workflow:`` block of factory.yaml. Pure, no I/O."""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any, Literal

DONE = "done"  # reserved target name: the task is finished
KINDS = ("agent", "human_gate")


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
    on_reject: str | None

    @property
    def targets(self) -> tuple[str, ...]:
        return tuple(t for t in (self.next, self.on_approve, self.on_reject) if t)


@dataclass(frozen=True)
class Workflow:
    start: str
    steps: Mapping[str, Step]

    def step(self, name: str) -> Step:
        try:
            return self.steps[name]
        except KeyError:
            raise WorkflowError(f"unknown step {name!r}") from None


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
    skill, artifacts, nxt, approve, reject = None, (), None, None, None
    if kind == "agent":
        skill = raw.get("skill")
        artifacts = tuple((raw.get("output") or {}).get("artifacts") or ())
        nxt = raw.get("next")
        if not skill:
            raise WorkflowError(f"step {name!r}: agent steps need a skill")
        if not artifacts:
            raise WorkflowError(f"step {name!r}: agent steps need output.artifacts")
        if not nxt:
            raise WorkflowError(f"step {name!r}: agent steps need next")
    else:
        approve, reject = raw.get("on_approve"), raw.get("on_reject")
        if not approve or not reject:
            raise WorkflowError(f"step {name!r}: gates need on_approve and on_reject")
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
    )


def parse_workflow(data: dict) -> Workflow:
    raw_steps = data.get("steps")
    if not isinstance(raw_steps, dict) or not raw_steps:
        raise WorkflowError("workflow needs a non-empty 'steps' mapping")
    steps = {str(n): _parse_step(str(n), raw) for n, raw in raw_steps.items()}
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

"""Parse and validate a workflow definition (workflow.yaml). Pure, no I/O."""

import copy
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Literal

from taktloop.checks import CallDecl, CheckDecl, parse_call, parse_check, placeholders

DONE = "done"  # reserved target name: the task is finished
CANCELLED = "cancelled"  # reserved target name: the task is cancelled
ROUTE_NAME = re.compile(r"[a-z][a-z_]*")
TASK_FIELDS = ("task.id", "task.title", "task.request")
KINDS = ("agent", "human_gate", "code")
ALLOWED_KEYS = {"kind", "role", "description", "skill", "instructions", "output", "next",
                "on_approve", "on_reject", "routes", "approves", "dispatch", "push", "check",
                "evidence", "on_fail", "call", "review_of", "on_pass", "on_issues",
                "max_issue_rounds", "on_escalate"}
REVIEW_KEYS = ("review_of", "on_pass", "on_issues", "max_issue_rounds", "on_escalate")


class WorkflowError(ValueError):
    """The workflow definition is invalid."""


@dataclass(frozen=True)
class Step:
    name: str
    kind: Literal["agent", "human_gate", "code"]
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
    evidence: tuple[str, ...] = ()
    check: CheckDecl | None = None
    on_fail: str | None = None
    call: CallDecl | None = None
    dispatch: Literal["pull", "push"] = "pull"
    push_identity: str | None = None
    description: str = ""
    routes: Mapping[str, str] = field(default_factory=dict)
    approves: str | None = None

    @property
    def is_review(self) -> bool:
        return self.review_of is not None

    @property
    def targets(self) -> tuple[str, ...]:
        named = (self.next, self.on_approve, *self.on_reject, self.review_of, self.on_pass,
                 self.on_issues, self.on_escalate, self.on_fail, *self.routes.values())
        return tuple(t for t in named if t)


@dataclass(frozen=True)
class Workflow:
    start: str
    steps: Mapping[str, Step]
    version: int = 1
    data: Mapping[str, Any] = field(default_factory=dict)  # the raw {start, steps} it was parsed from

    def step(self, name: str) -> Step:
        try:
            return self.steps[name]
        except KeyError:
            raise WorkflowError(f"unknown step {name!r}") from None

    def escalating_review(self, gate: str) -> Step | None:
        """The review step whose on_escalate is this gate, if any."""
        return next((s for s in self.steps.values() if s.on_escalate == gate), None)


def parse_step(name: str, raw: Any) -> Step:
    if not isinstance(raw, dict):
        raise WorkflowError(f"step {name!r}: definition must be a mapping")
    if name in (DONE, CANCELLED):
        raise WorkflowError(f"step name {name!r} is reserved")
    role = raw.get("role")
    if not role:
        raise WorkflowError(f"step {name!r}: role is required")
    kind = raw.get("kind")
    if kind not in KINDS:
        raise WorkflowError(
            f"step {name!r}: kind {kind!r} is not supported yet (supported: {', '.join(KINDS)})"
        )
    if unknown := sorted(set(raw) - ALLOWED_KEYS):
        raise WorkflowError(f"step {name!r}: unknown key {', '.join(unknown)}")
    for key in ("next", "on_approve", "on_reject", "on_fail", *(k for k in REVIEW_KEYS if k != "max_issue_rounds")):
        v = raw.get(key)
        if not (v is None or isinstance(v, str) or (key == "on_reject" and isinstance(v, list) and all(isinstance(t, str) for t in v))):
            raise WorkflowError(f"step {name!r}: {key} must be a step name")
    if kind == "code":
        return _parse_code_step(name, raw)
    dispatch, identity = _parse_push(name, raw, kind)
    skill, artifacts, nxt, approve, reject = None, (), None, None, ()
    routes, approves = _parse_routes(name, raw, kind)
    extra = _parse_check_fields(name, raw, kind)
    review = {k: raw.get(k) for k in REVIEW_KEYS}
    if kind == "agent":
        skill = raw.get("skill")
        out = raw.get("output") or {}
        artifacts = out.get("artifacts") or [] if isinstance(out, dict) else None
        if not isinstance(artifacts, list) or not all(isinstance(a, str) for a in artifacts):
            raise WorkflowError(f"step {name!r}: output must be a mapping with a list of artifact names")
        artifacts = tuple(artifacts)
        nxt = raw.get("next")
        if not skill:
            raise WorkflowError(f"step {name!r}: agent steps need a skill")
        if routes:
            bad = next((k for k in ("next", "output", "check", *REVIEW_KEYS) if raw.get(k) is not None), None)
            if bad:
                raise WorkflowError(f"step {name!r}: routing steps take no {bad}")
        elif any(v is not None for v in review.values()):
            _check_review(name, review, raw)
        elif not artifacts and not extra["check"]:
            raise WorkflowError(f"step {name!r}: agent steps need output.artifacts")
        elif not nxt:
            raise WorkflowError(f"step {name!r}: agent steps need next")
    else:
        approve, reject = raw.get("on_approve"), raw.get("on_reject")
        reject = (reject,) if isinstance(reject, str) else tuple(reject or ())
        if routes and (approve or reject):
            raise WorkflowError(f"step {name!r}: gates with routes take no on_approve or on_reject")
        if not routes and (not approve or not reject):
            raise WorkflowError(f"step {name!r}: gates need on_approve and on_reject")
        review = {}
    return Step(name=name, kind=kind, role=str(role), skill=skill, instructions=_text(raw, "instructions"),
                required_artifacts=artifacts, next=nxt, on_approve=approve, on_reject=reject,
                dispatch=dispatch, push_identity=identity, description=_text(raw, "description"),
                routes=routes, approves=approves, **review, **extra)


def _text(raw: dict, key: str) -> str:
    return " ".join(str(raw.get(key) or "").split())


def _parse_routes(name: str, raw: dict, kind: str) -> tuple[dict[str, str], str | None]:
    routes, approves = raw.get("routes"), raw.get("approves")
    if approves is not None and (kind != "human_gate" or routes is not None or not isinstance(approves, str)):
        raise WorkflowError(f"step {name!r}: approves is a step name, only on gates without routes")
    if routes is None:
        return {}, approves
    if (not isinstance(routes, dict) or not routes
            or not all(isinstance(k, str) and ROUTE_NAME.fullmatch(k) and isinstance(v, str)
                       for k, v in routes.items())):
        raise WorkflowError(f"step {name!r}: routes must map names like 'quick' to step names")
    return dict(routes), None


def _parse_push(name: str, raw: dict, kind: str) -> tuple[str, str | None]:
    dispatch, push = raw.get("dispatch", "pull"), raw.get("push") or {}
    if kind != "agent" and ("dispatch" in raw or "push" in raw):
        raise WorkflowError(f"step {name!r}: {kind} steps take no dispatch or push")
    if dispatch not in ("pull", "push"):
        raise WorkflowError(f"step {name!r}: dispatch must be pull or push, not {dispatch!r}")
    if dispatch == "pull":
        return "pull", None
    push = push if isinstance(push, dict) else {}
    if push.get("start") != "claude-headless":
        raise WorkflowError(f"step {name!r}: push.start {push.get('start')!r} is not supported "
                            "(supported: claude-headless)")
    if not push.get("identity"):
        raise WorkflowError(f"step {name!r}: push.identity is required")
    return "push", str(push["identity"])


def _parse_code_step(name: str, raw: dict) -> Step:
    for key in ("skill", "output", "instructions", "dispatch", "push", "routes", "approves", *REVIEW_KEYS):
        if raw.get(key) is not None:
            raise WorkflowError(f"step {name!r}: code steps take no {key}")
    if not raw.get("call") or not raw.get("next"):
        raise WorkflowError(f"step {name!r}: code steps need call and next")
    try:
        call = parse_call(raw["call"])
    except ValueError as e:
        raise WorkflowError(f"step {name!r}: {e}") from None
    extra = _parse_check_fields(name, raw, "code")
    if extra["evidence"] and set(extra["evidence"]) != set(call.evidence):
        raise WorkflowError(f"step {name!r}: call.evidence must produce exactly the step's evidence keys")
    extra["evidence"] = extra["evidence"] or tuple(call.evidence)
    return Step(name, "code", str(raw["role"]), None, "", (), raw["next"], None, (), call=call,
                description=_text(raw, "description"), **extra)


def _parse_check_fields(name: str, raw: dict, kind: str) -> dict:
    fields = ("check", "evidence", "on_fail")
    if all(raw.get(k) is None for k in fields):
        return {"check": None, "evidence": (), "on_fail": None}
    if kind not in ("agent", "code") or any(raw.get(k) is not None for k in REVIEW_KEYS):
        raise WorkflowError(f"step {name!r}: checks are allowed on agent work steps only")
    if any(not raw.get(k) for k in fields):
        raise WorkflowError(f"step {name!r}: check, evidence and on_fail go together")
    try:
        check = parse_check(raw["check"])
    except ValueError as e:
        raise WorkflowError(f"step {name!r}: {e}") from None
    evidence = raw["evidence"]
    if not isinstance(evidence, list) or not all(isinstance(e, str) for e in evidence):
        raise WorkflowError(f"step {name!r}: evidence must be a list of keys")
    return {"check": check, "evidence": tuple(evidence), "on_fail": str(raw["on_fail"])}


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


def _placeholder_problems(steps: Mapping[str, Step]) -> list[str]:
    artifacts = {a for s in steps.values() for a in s.required_artifacts}
    problems = []
    for step in steps.values():
        for decl in (step.check, step.call):
            for ph in sorted(placeholders(decl)) if decl else ():
                parts = ph.split(".")
                other = steps.get(parts[1]) if len(parts) > 2 else None
                ok = ph in TASK_FIELDS or (decl is step.check and parts[0] == "evidence"
                                           and ".".join(parts[1:]) in step.evidence) or (
                    decl is step.call and parts[0] == "evidence" and other is not None
                    and ".".join(parts[2:]) in other.evidence) or (
                    len(parts) == 3 and parts[0] == "artifacts" and parts[1] in artifacts
                    and parts[2] in ("commit", "branch", "path"))
                if not ok:
                    problems.append(f"step {step.name!r}: unknown placeholder {ph}")
    return problems


def graph_problems(steps: Mapping[str, Step], start: str | None) -> list[str]:
    """Everything wrong with the graph as a whole, sorted and human-readable."""
    problems = _placeholder_problems(steps)
    for step in steps.values():
        work = steps.get(step.review_of)
        if step.is_review and (work is None or work.kind != "agent" or work.is_review):
            problems.append(f"step {step.name!r}: review_of must name an agent work step")
        if step.approves is not None:
            target = steps.get(step.approves)
            if target is None or target.kind != "agent" or target.is_review or not target.required_artifacts:
                problems.append(f"step {step.name!r}: approves {step.approves!r} must name an agent "
                                "work step with artifacts")
        problems += [f"step {step.name!r} points to unknown step {t!r}"
                     for t in step.targets if t not in (DONE, CANCELLED, step.review_of) and t not in steps]
    if not isinstance(start, str) or start not in steps:
        return sorted([*problems, f"start step {start!r} does not exist"])
    seen, todo = set(), [start]
    while todo:
        name = todo.pop()
        if name not in seen and name in steps:
            seen.add(name)
            todo.extend(steps[name].targets)
    if seen != set(steps):
        problems.append(f"unreachable steps: {', '.join(sorted(set(steps) - seen))}")
    return sorted(problems)


def parse_workflow(data: dict, version: int = 1) -> Workflow:
    raw_steps = data.get("steps")
    if not isinstance(raw_steps, dict) or not raw_steps:
        raise WorkflowError("workflow needs a non-empty 'steps' mapping")
    steps = {str(n): parse_step(str(n), raw) for n, raw in raw_steps.items()}
    start = data.get("start")
    if problems := graph_problems(steps, start):
        raise WorkflowError("; ".join(problems))
    return Workflow(start, steps, version, copy.deepcopy({"start": start, "steps": raw_steps}))

"""Operations and rules: the only place that changes state and writes history."""

from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Literal

from taktloop.models import (
    Artifact, ArtifactIn, Claim, Event, OpenStep, StepContext, TaskDetail, TaskSummary,
)
from taktloop.rules import (
    Rejected, check_artifacts, clean_reason, latest_rounds, same_token, waiting_for,
)
from taktloop.store import ArtifactRow, StepRunRow, Store, TaskRow, Tx
from taktloop.workflow import DONE, Workflow

@dataclass(frozen=True)
class Caller:
    subject: str
    roles: frozenset[str]
    executor: str | None = None

    @property
    def label(self) -> str:
        return f"{self.subject} ({self.executor})" if self.executor else self.subject


@dataclass
class _Ids:
    """Task/step-run the current operation touched, for its history event."""
    task_id: int | None = None
    step_run_id: int | None = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Service:
    def __init__(self, store: Store, workflow: Workflow,
                 lease: timedelta = timedelta(minutes=30),
                 clock: Callable[[], datetime] = _utc_now) -> None:
        self._store, self._wf, self._lease, self._clock = store, workflow, lease, clock

    # --- shared helpers -------------------------------------------------

    def _event(self, tx: Tx, who: str, operation: str, ids: _Ids, detail: dict,
               result: str = "ok", reason: str | None = None) -> None:
        tx.insert_event(self._clock(), who, operation, ids.task_id, ids.step_run_id,
                        detail, result, reason)

    def _run(self, caller: Caller, operation: str, detail: dict[str, Any],
             body: Callable[[Tx, _Ids], Any], *, writes: bool = True) -> Any:
        """One transaction per operation; rejections are rolled back, then recorded."""
        ids = _Ids()
        try:
            with self._store.transaction() as tx:
                result = body(tx, ids)
                if writes:
                    self._event(tx, caller.label, operation, ids, detail)
                return result
        except Rejected as r:
            with self._store.transaction() as tx:
                self._event(tx, caller.label, operation, ids, detail, "rejected", r.reason)
            raise

    def _expire(self, tx: Tx, run: StepRunRow) -> StepRunRow:
        """Lazy lease expiry: an overdue claimed run becomes open again."""
        if run.status != "claimed" or run.lease_expires_at > self._clock():
            return run
        tx.update_step_run(run.id, status="open", claimed_by=None, claim_token=None,
                           lease_expires_at=None)
        self._event(tx, "core", "lease_expired", _Ids(run.task_id, run.id),
                    {"step_id": run.id, "was_claimed_by": run.claimed_by})
        return tx.get_step_run(run.id)

    def _load(self, tx: Tx, ids: _Ids, step_id: int) -> StepRunRow:
        run = tx.get_step_run(step_id)
        if run is None:
            raise Rejected(f"unknown step {step_id}")
        ids.task_id, ids.step_run_id = run.task_id, run.id
        return self._expire(tx, run)

    def _load_owned(self, tx: Tx, ids: _Ids, step_id: int, claim_token: str) -> StepRunRow:
        run = self._load(tx, ids, step_id)
        if run.status != "claimed":
            raise Rejected("step is not claimed (lease expired or released); claim it again")
        if not same_token(run.claim_token, claim_token):
            raise Rejected("claim token does not match")
        return run

    def _summary(self, tx: Tx, task: TaskRow) -> TaskSummary:
        run = None
        if task.status != "done":
            run = tx.current_step_run(task.id)
            run = self._expire(tx, run) if run else None
        return TaskSummary(
            id=task.id, title=task.title, status=task.status,
            current_step=run.step if run else None, step_id=run.id if run else None,
            waiting_for=waiting_for(
                self._wf.step(run.step) if run else None,
                run.status if run else "open", run.claimed_by if run else None,
                run.lease_expires_at if run else None))

    def _open_step(self, tx: Tx, task_id: int, step: str) -> int:
        last = tx.last_step_run(task_id, step)
        return tx.insert_step_run(task_id, step, (last.round if last else 0) + 1, self._clock())

    def _artifacts(self, tx: Tx, task_id: int) -> list[Artifact]:
        out = []
        for a in tx.list_artifacts(task_id):
            run = tx.get_step_run(a.step_run_id)
            out.append(Artifact(name=a.name, path=a.path, commit=a.commit, branch=a.branch,
                                step=run.step, round=run.round))
        return out

    def _claim(self, tx: Tx, run: StepRunRow) -> Claim:
        expires = self._clock() + self._lease
        tx.update_step_run(run.id, lease_expires_at=expires)
        return Claim(step_id=run.id, claim_token=run.claim_token, lease_expires_at=expires)

    # --- operations -----------------------------------------------------

    def create_task(self, caller: Caller, title: str, request: str = "") -> TaskSummary:
        def body(tx: Tx, ids: _Ids) -> TaskSummary:
            if not title.strip():
                raise Rejected("title must not be empty")
            ids.task_id = tx.insert_task(title.strip(), request, caller.label, self._clock())
            ids.step_run_id = self._open_step(tx, ids.task_id, self._wf.start)
            return self._summary(tx, tx.get_task(ids.task_id))
        return self._run(caller, "create_task", {"title": title, "request": request}, body)

    def list_open_steps(self, caller: Caller) -> list[OpenStep]:
        def body(tx: Tx, ids: _Ids) -> list[OpenStep]:
            for run in tx.step_runs_with_status(["claimed"]):
                self._expire(tx, run)
            out = []
            for run in tx.step_runs_with_status(["open"]):
                step = self._wf.step(run.step)
                if step.kind == "agent":
                    out.append(OpenStep(step_id=run.id, task_id=run.task_id,
                                        task_title=tx.get_task(run.task_id).title,
                                        step=run.step, skill=step.skill, round=run.round))
            return out
        return self._run(caller, "list_open_steps", {}, body, writes=False)

    def claim_step(self, caller: Caller, step_id: int) -> Claim:
        def body(tx: Tx, ids: _Ids) -> Claim:
            run = self._load(tx, ids, step_id)
            if run.status == "claimed":
                raise Rejected("step is already claimed")
            if run.status != "open":
                raise Rejected(f"step is not open (status: {run.status})")
            if self._wf.step(run.step).kind != "agent":
                raise Rejected("step is a human gate and cannot be claimed")
            tx.update_step_run(run.id, status="claimed", claimed_by=caller.label,
                               claim_token=secrets.token_hex(16))
            return self._claim(tx, tx.get_step_run(run.id))
        return self._run(caller, "claim_step", {"step_id": step_id}, body)

    def get_step_context(self, caller: Caller, step_id: int) -> StepContext:
        def body(tx: Tx, ids: _Ids) -> StepContext:
            run = self._load(tx, ids, step_id)
            task, step = tx.get_task(run.task_id), self._wf.step(run.step)
            reason = None
            if run.round > 1:
                rejects = [e for e in tx.list_events(task.id) if e.operation == "decide_gate"
                           and e.result == "ok" and e.detail.get("decision") == "reject"]
                reason = rejects[-1].detail["reason"] if rejects else None
            return StepContext(
                step_id=run.id, task_id=task.id, task_title=task.title, request=task.request,
                step=run.step, skill=step.skill or "", instructions=step.instructions,
                required_artifacts=list(step.required_artifacts), round=run.round,
                rejection_reason=reason,
                artifacts=latest_rounds(self._artifacts(tx, task.id), exclude_step=run.step if run.round == 1 else None))
        return self._run(caller, "get_step_context", {"step_id": step_id}, body, writes=False)

    def heartbeat(self, caller: Caller, step_id: int, claim_token: str) -> Claim:
        def body(tx: Tx, ids: _Ids) -> Claim:
            return self._claim(tx, self._load_owned(tx, ids, step_id, claim_token))
        return self._run(caller, "heartbeat", {"step_id": step_id}, body)

    def complete_step(self, caller: Caller, step_id: int, claim_token: str, outcome: str,
                      artifacts: list[ArtifactIn]) -> TaskSummary:
        def body(tx: Tx, ids: _Ids) -> TaskSummary:
            run = self._load_owned(tx, ids, step_id, claim_token)
            step = self._wf.step(run.step)
            if outcome != "completed":
                raise Rejected(f"outcome must be 'completed', got {outcome!r}")
            check_artifacts(step.required_artifacts, artifacts)
            for a in artifacts:
                tx.insert_artifact(ArtifactRow(run.task_id, run.id, a.name, a.path, a.commit,
                                               a.branch))
            tx.update_step_run(run.id, status="completed", outcome=outcome,
                               closed_at=self._clock())
            if step.next == DONE:
                tx.set_task_status(run.task_id, "done")
            else:
                self._open_step(tx, run.task_id, step.next)
            return self._summary(tx, tx.get_task(run.task_id))
        detail = {"step_id": step_id, "outcome": outcome,
                  "artifacts": [a.model_dump() for a in artifacts]}
        return self._run(caller, "complete_step", detail, body)

    def release_step(self, caller: Caller, step_id: int, reason: str,
                     claim_token: str | None = None, force: bool = False) -> TaskSummary:
        def body(tx: Tx, ids: _Ids) -> TaskSummary:
            run = self._load(tx, ids, step_id)
            if not reason.strip():
                raise Rejected("reason is required")
            if run.status != "claimed":
                raise Rejected("step is not claimed")
            if not force and not (claim_token and same_token(run.claim_token, claim_token)):
                raise Rejected("claim token does not match")
            tx.update_step_run(run.id, status="open", claimed_by=None, claim_token=None,
                               lease_expires_at=None)
            return self._summary(tx, tx.get_task(run.task_id))
        detail = {"step_id": step_id, "reason": reason, "force": force}
        return self._run(caller, "release_step", detail, body)

    def decide_gate(self, caller: Caller, task_id: int, gate: str | None,
                    decision: Literal["approve", "reject"],
                    reason: str | None = None) -> TaskSummary:
        names: dict = {"task_id": task_id, "gate": gate, "decision": decision,
                       "reason": reason, "artifacts": []}

        def body(tx: Tx, ids: _Ids) -> TaskSummary:
            task = tx.get_task(task_id)
            if task is None:
                raise Rejected(f"unknown task {task_id}")
            ids.task_id = task_id
            run = tx.current_step_run(task_id)
            run = self._expire(tx, run) if run else None
            if run is None or self._wf.step(run.step).kind != "human_gate" or run.status != "open":
                raise Rejected(f"task {task_id} is not at a gate")
            ids.step_run_id = run.id
            names["gate"] = run.step
            if gate is not None and gate != run.step:
                raise Rejected(f"task {task_id} is at gate {run.step}, not {gate}")
            step = self._wf.step(run.step)
            if decision == "approve":
                target, outcome = step.on_approve, "approved"
            elif decision == "reject":
                names["reason"] = clean_reason(reason)
                target, outcome = step.on_reject, "rejected"
            else:
                raise Rejected(f"decision must be 'approve' or 'reject', got {decision!r}")
            names["artifacts"] = [a.model_dump() for a in
                                  latest_rounds(self._artifacts(tx, task_id))]
            tx.update_step_run(run.id, status="completed", outcome=outcome,
                               closed_at=self._clock())
            if target == DONE:
                tx.set_task_status(task_id, "done")
            else:
                self._open_step(tx, task_id, target)
            return self._summary(tx, tx.get_task(task_id))
        return self._run(caller, "decide_gate", names, body)

    def get_task(self, caller: Caller, task_id: int) -> TaskDetail:
        def body(tx: Tx, ids: _Ids) -> TaskDetail:
            task = tx.get_task(task_id)
            if task is None:
                raise Rejected(f"unknown task {task_id}")
            ids.task_id = task_id
            summary = self._summary(tx, task)
            steps: dict[int | None, str | None] = {None: None}
            history = []
            for e in tx.list_events(task_id):
                if e.step_run_id not in steps:
                    steps[e.step_run_id] = tx.get_step_run(e.step_run_id).step
                history.append(Event(at=e.at, caller=e.caller, operation=e.operation,
                                     step=steps[e.step_run_id], result=e.result,
                                     reason=e.reason, detail=e.detail))
            return TaskDetail(**summary.model_dump(), request=task.request,
                              artifacts=self._artifacts(tx, task_id), history=history)
        return self._run(caller, "get_task", {"task_id": task_id}, body, writes=False)

    def list_tasks(self, caller: Caller, include_closed: bool = False) -> list[TaskSummary]:
        def body(tx: Tx, ids: _Ids) -> list[TaskSummary]:
            statuses = ["active", "done", "cancelled"] if include_closed else ["active"]
            return [self._summary(tx, t) for t in tx.list_tasks(statuses)]
        return self._run(caller, "list_tasks", {"include_closed": include_closed}, body,
                         writes=False)

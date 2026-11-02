"""Shared base for the service operations: caller, transactions, expiry, loading."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from taktloop.models import Artifact, Claim, TaskSummary
from taktloop.permissions import require
from taktloop.rules import Rejected, same_token, waiting_for
from taktloop.separation import Record
from taktloop.store import StepRunRow, Store, TaskRow, Tx
from taktloop.workflow import DONE, Workflow


@dataclass(frozen=True)
class Caller:
    subject: str
    roles: frozenset[str]
    executor: str | None = None

    @property
    def label(self) -> str:
        return _label(self.subject, self.executor)


def _label(subject: str | None, executor: str | None) -> str | None:
    return f"{subject} ({executor})" if executor else subject


CORE = Caller("core", frozenset())


@dataclass
class _Ids:
    """Task/step-run the current operation touched, for its history event."""
    task_id: int | None = None
    step_run_id: int | None = None
    core_events: list[tuple[str, int | None, dict]] = field(default_factory=list)
    """What the core did as a consequence (operation, step run, detail), recorded after."""


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


class ServiceBase:
    def __init__(self, store: Store, workflow: Workflow,
                 lease: timedelta = timedelta(minutes=30),
                 clock: Callable[[], datetime] = _utc_now, separation: str = "off") -> None:
        self._store, self._wf, self._lease, self._clock = store, workflow, lease, clock
        self._separation = separation

    # --- shared helpers -------------------------------------------------

    def _event(self, tx: Tx, who: Caller, operation: str, ids: _Ids, detail: dict,
               result: str = "ok", reason: str | None = None) -> None:
        tx.insert_event(self._clock(), who.subject, sorted(who.roles), who.executor, operation,
                        ids.task_id, ids.step_run_id, detail, result, reason)

    def _run(self, caller: Caller, operation: str, detail: dict[str, Any],
             body: Callable[[Tx, _Ids], Any], *, writes: bool = True,
             task_id: int | None = None) -> Any:
        """One transaction per operation; rejections are rolled back, then recorded.
        Step-bound roles are checked again by the body once it has loaded the step.
        `task_id`, when the operation names one, files even a role refusal under that task."""
        ids = _Ids(task_id)
        try:
            require(caller, operation)  # before anything else, even lease expiry
            with self._store.transaction() as tx:  # own txn: expiry survives a rejected op
                for run in tx.step_runs_with_status(["claimed"]):
                    self._expire(tx, run)
            with self._store.transaction() as tx:
                result = body(tx, ids)
                if writes:
                    self._event(tx, caller, operation, ids, detail)
                for op, run_id, d in ids.core_events:
                    self._event(tx, CORE, op, _Ids(ids.task_id, run_id), d)
                return result
        except Rejected as r:
            with self._store.transaction() as tx:
                self._event(tx, caller, operation, ids, detail, "rejected", r.reason)
            raise

    def _separate(self, tx: Tx, task_id: int,
                  rule: Callable[[list[Record]], str | None]) -> None:
        """In strict mode, refuse with the rule's text if it objects to the task's records.
        Runs an override closed as superseded were never completed and do not count."""
        if self._separation != "strict":
            return
        refusal = rule([Record(r.step, r.round, r.claimed_by, task_id)
                        for r in tx.list_step_runs(task_id)
                        if r.status == "completed" and r.outcome != "superseded"
                        and r.claimed_by])
        if refusal:
            raise Rejected(refusal)

    def _expire(self, tx: Tx, run: StepRunRow) -> StepRunRow:
        """Lazy lease expiry: an overdue claimed run becomes open again."""
        if run.status != "claimed" or run.lease_expires_at > self._clock():
            return run
        tx.update_step_run(run.id, status="open", claimed_by=None, claimed_executor=None,
                           claim_token=None, lease_expires_at=None)
        self._event(tx, CORE, "lease_expired", _Ids(run.task_id, run.id),
                    {"step_id": run.id, "was_claimed_by": _holder(run)})
        return tx.get_step_run(run.id)

    def _load(self, tx: Tx, ids: _Ids, step_id: int) -> StepRunRow:
        run = tx.get_step_run(step_id)
        if run is None:
            raise Rejected(f"unknown step {step_id}")
        ids.task_id, ids.step_run_id = run.task_id, run.id
        return self._expire(tx, run)

    def _load_owned(self, tx: Tx, ids: _Ids, caller: Caller, operation: str, step_id: int,
                    claim_token: str) -> StepRunRow:
        run = self._load(tx, ids, step_id)
        require(caller, operation, self._wf.step(run.step))
        if run.status != "claimed":
            raise Rejected("step is not claimed (lease expired or released); claim it again")
        _check_owner(run, caller, claim_token)
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
                run.status if run else "open", _holder(run) if run else None,
                run.lease_expires_at if run else None,
                self._wf.escalating_review(run.step) if run else None))

    def _open_step(self, tx: Tx, task_id: int, step: str) -> int:
        last = tx.last_step_run(task_id, step)
        return tx.insert_step_run(task_id, step, (last.round if last else 0) + 1, self._clock())

    def _advance(self, tx: Tx, task_id: int, target: str) -> int | None:
        """Open `target` as the task's next run (its id), or finish the task."""
        if target == DONE:
            tx.set_task_status(task_id, "done")
            return None
        return self._open_step(tx, task_id, target)

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


def _holder(run: StepRunRow) -> str | None:
    return _label(run.claimed_by, run.claimed_executor)


def _check_owner(run: StepRunRow, caller: Caller, claim_token: str | None) -> None:
    if run.claimed_by != caller.subject:
        raise Rejected(f"step is claimed by {run.claimed_by}, not you")
    if not (claim_token and same_token(run.claim_token, claim_token)):
        raise Rejected("claim token does not match")

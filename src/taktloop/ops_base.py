"""Shared base for the service operations: caller, transactions, expiry, loading."""

from __future__ import annotations

from collections.abc import Callable, Collection
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from taktloop.availability import Availability
from taktloop.external import Checker
from taktloop.models import Artifact, Claim, TaskSummary
from taktloop.permissions import require
from taktloop.rules import Rejected, same_token, waiting_for
from taktloop.separation import Record
from taktloop.store import StepRunRow, Store, TaskRow, Tx
from taktloop.workflow import CANCELLED, DONE, Workflow, parse_workflow
from taktloop.workflow_file import data_sha


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
                 clock: Callable[[], datetime] = _utc_now, separation: str = "off",
                 checker: Checker | None = None, servers: Collection[str] = (),
                 workflow_path: Path | None = None,
                 tools_check: Callable[[Workflow], str | None] | None = None,
                 availability: Availability | None = None) -> None:
        self._availability = availability
        self._servers, self._workflow_path, self._tools_check = set(servers), workflow_path, tools_check
        self._store, self._lease, self._clock = store, lease, clock
        self._separation, self._checker = separation, checker
        self._versions: dict[int, Workflow] = {}
        self._reconcile(workflow)

    def _reconcile(self, workflow: Workflow) -> None:
        """Snapshot the file's workflow: as its own version on first start, as the next version
        when it differs from the latest snapshot (a hand edit)."""
        with self._store.transaction() as tx:
            latest, sha = tx.latest_workflow_version(), data_sha(workflow.data)
            if latest is None or latest.sha256 != sha:
                if latest:
                    workflow = parse_workflow(workflow.data, latest.version + 1)
                tx.insert_workflow_version(workflow.version, workflow.data, sha, "file", None,
                                           self._clock())
                if latest:
                    self._event(tx, CORE, "workflow_changed", _Ids(),
                                {"version": workflow.version, "source": "file"})
            else:
                workflow = parse_workflow(latest.data, latest.version)
        self.live_version = workflow.version
        self._versions[workflow.version] = workflow

    live = property(lambda self: self._versions[self.live_version])

    def live_data(self) -> dict:
        return self.live.data

    def _version(self, tx: Tx, version: int) -> Workflow:
        if version not in self._versions:
            self._versions[version] = parse_workflow(
                tx.get_workflow_version(version).data, version)
        return self._versions[version]

    def _wf_of(self, tx: Tx, task_id: int) -> Workflow:
        return self._version(tx, tx.get_task(task_id).workflow_version)

    def _live(self, tx: Tx) -> Workflow:
        return self._version(tx, self.live_version)

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
        require(caller, operation, self._wf_of(tx, run.task_id).step(run.step))
        if run.status != "claimed":
            raise Rejected("step is not claimed (lease expired or released); claim it again")
        _check_owner(run, caller, claim_token)
        return run

    def _summary(self, tx: Tx, task: TaskRow) -> TaskSummary:
        run = None
        if task.status == "active":
            run = tx.current_step_run(task.id)
            run = self._expire(tx, run) if run else None
        wf = self._wf_of(tx, task.id)
        return TaskSummary(
            id=task.id, title=task.title, status=task.status,
            current_step=run.step if run else None, step_id=run.id if run else None,
            waiting_for="cancelled" if task.status == "cancelled" else waiting_for(
                wf.step(run.step) if run else None,
                run.status if run else "open", _holder(run) if run else None,
                run.lease_expires_at if run else None,
                wf.escalating_review(run.step) if run else None,
                _stall_reason(tx, run) if run else None))

    def _open_step(self, tx: Tx, task_id: int, step: str) -> int:
        """Open a run of `step`, recording step_opened. A routed agent step in round 1 of a task
        created with a route is completed at once, the creator's route standing in for triage."""
        last = tx.last_step_run(task_id, step)
        rnd = (last.round if last else 0) + 1
        run_id = tx.insert_step_run(task_id, step, rnd, self._clock())
        self._event(tx, CORE, "step_opened", _Ids(task_id, run_id), {"step": step, "round": rnd})
        task = tx.get_task(task_id)
        s = self._wf_of(tx, task_id).step(step)
        if rnd == 1 and s.kind == "agent" and task.route in s.routes:
            by = task.created_by
            tx.update_step_run(run_id, status="completed", claimed_by=by.split(" (")[0], outcome=task.route,
                               evidence={"findings": f"route set at intake by {by}"},
                               closed_at=self._clock())
            self._event(tx, CORE, "route_at_intake", _Ids(task_id, run_id),
                        {"step": step, "route": task.route, "by": by})
            self._advance(tx, task_id, s.routes[task.route])
        return run_id

    def _advance(self, tx: Tx, task_id: int, target: str) -> int | None:
        """Open `target` as the task's next run (its id), or finish or cancel the task."""
        if target in (DONE, CANCELLED):
            tx.set_task_status(task_id, target)
            self._event(tx, CORE, "task_closed", _Ids(task_id), {"status": target})
            return None
        return self._open_step(tx, task_id, target)

    def _values(self, tx: Tx, task_id: int) -> dict[str, str]:
        """task.* and evidence.<step>.<key> (latest completed run of each step) for substitution."""
        task = tx.get_task(task_id)
        out = {"task.id": str(task.id), "task.title": task.title, "task.request": task.request}
        latest = {r.step: r for r in tx.list_step_runs(task_id) if r.status == "completed"}
        return out | {f"evidence.{r.step}.{k}": str(v) for r in latest.values()
                      for k, v in (r.evidence or {}).items()}

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


def _stall_reason(tx: Tx, run: StepRunRow) -> str | None:
    """The reason the orchestrator reported this run stalled, if it did."""
    return next((e.detail["reason"] for e in tx.list_events(run.task_id)
                 if e.step_run_id == run.id and e.operation == "report_stall"
                 and e.result == "ok"), None)


def _holder(run: StepRunRow) -> str | None:
    return _label(run.claimed_by, run.claimed_executor)


def _check_owner(run: StepRunRow, caller: Caller, claim_token: str | None) -> None:
    if run.claimed_by != caller.subject:
        raise Rejected(f"step is claimed by {run.claimed_by}, not you")
    if not (claim_token and same_token(run.claim_token, claim_token)):
        raise Rejected("claim token does not match")

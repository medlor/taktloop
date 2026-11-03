"""Orchestrator operations: sweep the open work, report a stall."""

from __future__ import annotations

from taktloop.models import EventFeed, FeedEvent, SweptStep, TaskSummary
from taktloop.ops_base import Caller, ServiceBase, _Ids, _stall_reason
from taktloop.rules import Rejected, clean_reason
from taktloop.store import Tx


class OrchestratorOps(ServiceBase):
    def sweep(self, caller: Caller) -> list[SweptStep]:
        def body(tx: Tx, ids: _Ids) -> list[SweptStep]:
            out = []
            for run in tx.step_runs_with_status(["open", "claimed"]):
                step = self._wf.step(run.step)
                mine = [e for e in tx.list_events(run.task_id) if e.step_run_id == run.id
                        and e.result == "ok"]
                idle = [e.at for e in mine if e.operation in ("lease_expired", "release_step")]
                out.append(SweptStep(
                    step_id=run.id, task_id=run.task_id, step=run.step, kind=step.kind,
                    role=step.role, dispatch=step.dispatch, push_identity=step.push_identity,
                    status=run.status, claimed_by=run.claimed_by,
                    idle_since=max(idle, default=run.opened_at),
                    lease_expiries=sum(e.operation == "lease_expired" for e in mine),
                    stalled=_stall_reason(tx, run) is not None))
            return out
        return self._run(caller, "sweep", {}, body, writes=False)

    def list_events(self, caller: Caller, after_id: int, limit: int = 100) -> EventFeed:
        """Successful events after `after_id`, oldest first, with the task's template values."""
        def body(tx: Tx, ids: _Ids) -> EventFeed:
            feed = [FeedEvent(
                id=e.id, at=e.at, subject=e.subject, operation=e.operation, task_id=e.task_id,
                step=e.step_run_id and tx.get_step_run(e.step_run_id).step, detail=e.detail,
                values=self._values(tx, e.task_id) if e.task_id else {})
                for e in tx.events_after(after_id, limit)]
            return EventFeed(events=feed, newest_id=feed[-1].id if feed else after_id)
        return self._run(caller, "list_events", {}, body, writes=False)

    def report_stall(self, caller: Caller, step_id: int, reason: str) -> TaskSummary:
        def body(tx: Tx, ids: _Ids) -> TaskSummary:
            run, why = self._load(tx, ids, step_id), clean_reason(reason)
            if run.status not in ("open", "claimed"):
                raise Rejected(f"step is not open (status: {run.status})")
            if _stall_reason(tx, run) is None:  # a second report is accepted and ignored
                self._event(tx, caller, "report_stall", ids,
                            {"step_id": step_id, "reason": why})
            return self._summary(tx, tx.get_task(run.task_id))
        return self._run(caller, "report_stall", {"step_id": step_id, "reason": reason}, body,
                         writes=False)

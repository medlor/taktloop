"""Gate operations: human decisions at gates."""

from __future__ import annotations

from typing import Literal

from taktloop.models import TaskSummary
from taktloop.ops_base import Caller, ServiceBase, _Ids
from taktloop.permissions import require
from taktloop.rules import Rejected, clean_reason, latest_rounds
from taktloop.store import Tx
from taktloop.workflow import DONE


class GateOps(ServiceBase):
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
            require(caller, "decide_gate", self._wf.step(run.step))
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

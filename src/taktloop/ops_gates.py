"""Gate operations: human decisions at gates."""

from __future__ import annotations

from typing import Literal

from taktloop.models import TaskSummary
from taktloop.ops_base import Caller, ServiceBase, _holder, _Ids
from taktloop.permissions import require
from taktloop.rules import Rejected, clean_reason, latest_rounds
from taktloop.separation import approved_step, check_approval
from taktloop.store import Tx


class GateOps(ServiceBase):
    def decide_gate(self, caller: Caller, task_id: int, gate: str | None,
                    decision: Literal["approve", "reject", "route"],
                    reason: str | None = None, target: str | None = None) -> TaskSummary:
        """On reject, `target` must be one of the gate's on_reject steps (default: the first).
        A gate with routes takes only `route`: `target` is a route name, `reason` required."""
        names: dict = {"task_id": task_id, "gate": gate, "decision": decision,
                       "reason": reason, "target": target, "artifacts": []}

        def body(tx: Tx, ids: _Ids) -> TaskSummary:
            self._task(tx, ids, task_id)
            run, wf = self._current(tx, task_id), self._wf_of(tx, task_id)
            step = wf.step(run.step) if run else None
            if run is None or step.kind != "human_gate" or run.status != "open":
                raise Rejected(f"task {task_id} is not at a gate")
            ids.step_run_id, names["gate"] = run.id, run.step
            require(caller, "decide_gate", step)
            if gate is not None and gate != run.step:
                raise Rejected(f"task {task_id} is at gate {run.step}, not {gate}")
            approved = approved_step(wf, step, None)
            self._separate(tx, task_id,
                           lambda recs: check_approval(approved, caller.subject, recs))
            if step.routes or decision == "route":
                if decision != "route" or not step.routes:
                    raise Rejected(f"gate {step.name} takes decision 'route' (one of: "
                                   f"{', '.join(step.routes)})" if step.routes else
                                   f"gate {step.name} has no routes")
                names["reason"] = clean_reason(reason)
                dest, outcome = step.routes.get(target or ""), target
                if dest is None:
                    raise Rejected(f"route must be one of: {', '.join(step.routes)}")
            elif decision == "approve":
                dest, outcome = step.on_approve, "approved"
            elif decision == "reject":
                names["reason"] = clean_reason(reason)
                dest, outcome = target or step.on_reject[0], "rejected"
                if dest not in step.on_reject:
                    raise Rejected(f"gate {step.name} can send back to "
                                   f"{', '.join(step.on_reject)}; not {dest}")
                names["target"] = dest
            else:
                raise Rejected(f"decision must be 'approve', 'reject' or 'route', got {decision!r}")
            names["artifacts"] = [a.model_dump() for a in
                                  latest_rounds(self._artifacts(tx, task_id))]
            tx.update_step_run(run.id, status="completed", outcome=outcome,
                               closed_at=self._clock())
            self._advance(tx, task_id, dest)
            return self._summary(tx, tx.get_task(task_id))
        return self._run(caller, "decide_gate", names, body, task_id=task_id)

    def override_review(self, caller: Caller, task_id: int, review_step: str,
                        reason: str) -> TaskSummary:
        """Approve the latest completed round of the review step's review_of, whatever the
        verdicts (spec 6.3). Closes the current run as superseded; verdicts stay as they are."""
        names: dict = {"task_id": task_id, "review_step": review_step, "reason": reason}

        def body(tx: Tx, ids: _Ids) -> TaskSummary:
            self._task(tx, ids, task_id)
            wf = self._wf_of(tx, task_id)
            review = wf.steps.get(review_step)
            if review is None or not review.is_review:
                raise Rejected(f"{review_step} is not a review step")
            names["reason"] = clean_reason(reason)
            runs = tx.list_step_runs(task_id)
            verdicts = [r for r in runs
                        if r.step == review.name and r.outcome in ("pass", "issues")]
            run = self._current(tx, task_id)
            if run is None or not (run.step in (review.name, review.on_escalate) or (
                    run.step == review.review_of and verdicts and run.id > verdicts[0].id)):
                raise Rejected(f"task {task_id} has no review of {review.review_of} to override")
            ids.step_run_id = run.id
            approved = approved_step(wf, None, review)
            self._separate(tx, task_id,
                           lambda recs: check_approval(approved, caller.subject, recs))
            if run.status == "claimed":
                names["cancelled_claim"] = _holder(run)
            names["verdicts"] = [{"outcome": r.outcome, "by": r.claimed_by, "round": r.round}
                                 for r in verdicts]
            names["approved"] = [a.model_dump() for a in latest_rounds(
                a for a in self._artifacts(tx, task_id) if a.step == review.review_of)]
            tx.update_step_run(run.id, status="completed", outcome="superseded",
                               closed_at=self._clock())
            self._advance(tx, task_id, review.on_pass)
            return self._summary(tx, tx.get_task(task_id))
        return self._run(caller, "override_review", names, body, task_id=task_id)

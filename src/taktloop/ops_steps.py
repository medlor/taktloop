"""Step operations: claim, heartbeat, complete, release."""

from __future__ import annotations

import secrets

from taktloop.models import ArtifactIn, Claim, TaskSummary
from taktloop.ops_base import Caller, ServiceBase, _check_owner, _holder, _Ids
from taktloop.permissions import require
from taktloop.rules import Rejected, check_artifacts
from taktloop.store import ArtifactRow, Tx
from taktloop.workflow import DONE


class StepOps(ServiceBase):
    def claim_step(self, caller: Caller, step_id: int) -> Claim:
        def body(tx: Tx, ids: _Ids) -> Claim:
            run = self._load(tx, ids, step_id)
            require(caller, "claim_step", self._wf.step(run.step))
            if run.status == "claimed":
                raise Rejected("step is already claimed")
            if run.status != "open":
                raise Rejected(f"step is not open (status: {run.status})")
            if self._wf.step(run.step).kind != "agent":
                raise Rejected("step is a human gate and cannot be claimed")
            tx.update_step_run(run.id, status="claimed", claimed_by=caller.subject,
                               claimed_executor=caller.executor,
                               claim_token=secrets.token_hex(16))
            return self._claim(tx, tx.get_step_run(run.id))
        return self._run(caller, "claim_step", {"step_id": step_id}, body)

    def heartbeat(self, caller: Caller, step_id: int, claim_token: str) -> Claim:
        def body(tx: Tx, ids: _Ids) -> Claim:
            return self._claim(tx, self._load_owned(tx, ids, caller, "heartbeat", step_id,
                                                    claim_token))
        return self._run(caller, "heartbeat", {"step_id": step_id}, body)

    def complete_step(self, caller: Caller, step_id: int, claim_token: str, outcome: str,
                      artifacts: list[ArtifactIn]) -> TaskSummary:
        def body(tx: Tx, ids: _Ids) -> TaskSummary:
            run = self._load_owned(tx, ids, caller, "complete_step", step_id, claim_token)
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
            require(caller, "release_step --force" if force else "release_step",
                    self._wf.step(run.step))
            if not reason.strip():
                raise Rejected("reason is required")
            if run.status != "claimed":
                raise Rejected("step is not claimed (already free)")
            if not force:
                _check_owner(run, caller, claim_token)
            detail["was_claimed_by"] = _holder(run)
            tx.update_step_run(run.id, status="open", claimed_by=None, claimed_executor=None,
                               claim_token=None, lease_expires_at=None)
            return self._summary(tx, tx.get_task(run.task_id))
        detail = {"step_id": step_id, "reason": reason, "force": force}
        return self._run(caller, "release_step", detail, body)


"""Step operations: claim, heartbeat, complete, release."""

from __future__ import annotations

import secrets

from taktloop.checks import (CheckDecl, CheckUnavailable, Mismatch, compare, describe, lookup,
                             resolve)
from taktloop.models import ArtifactIn, Claim, TaskSummary
from taktloop.ops_base import CORE, Caller, ServiceBase, _check_owner, _holder, _Ids
from taktloop.permissions import require
from taktloop.rules import Rejected, check_artifacts, latest_rounds
from taktloop.separation import check_claim
from taktloop.store import ArtifactRow, Tx
from taktloop.workflow import Step


class StepOps(ServiceBase):
    def claim_step(self, caller: Caller, step_id: int) -> Claim:
        def body(tx: Tx, ids: _Ids) -> Claim:
            run = self._load(tx, ids, step_id)
            require(caller, "claim_step", self._wf.step(run.step))
            if run.status == "claimed":
                raise Rejected("step is already claimed")
            if run.status != "open":
                raise Rejected(f"step is not open (status: {run.status})")
            if self._wf.step(run.step).kind not in ("agent", "code"):
                raise Rejected("step is a human gate and cannot be claimed")
            self._separate(tx, run.task_id, lambda recs: check_claim(
                self._wf, self._wf.step(run.step), caller.subject, recs))
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
                      artifacts: list[ArtifactIn], findings: str | None = None,
                      evidence: dict[str, str] | None = None) -> TaskSummary:
        """Work steps: outcome `completed` with artifacts. Review steps: `pass` or `issues`,
        no artifacts, findings required for `issues`. Steps with a check need evidence (_check)."""
        evidence = evidence or {}

        def validate(tx: Tx, ids: _Ids) -> tuple[int, CheckDecl | None]:
            run = self._load_owned(tx, ids, caller, "complete_step", step_id, claim_token)
            step = self._wf.step(run.step)
            _check_evidence(step.evidence, evidence)
            if not step.check:
                return run.task_id, None
            if outcome != "completed":
                raise Rejected(f"outcome must be 'completed', got {outcome!r}")
            if artifacts and not step.required_artifacts:
                raise Rejected("this step takes no artifacts")
            arts = {a.name: a for a in latest_rounds(self._artifacts(tx, run.task_id))}
            try:  # the check's arguments and wanted values
                return run.task_id, resolve(step.check, evidence, arts)
            except CheckUnavailable as e:
                raise Rejected(f"CI check could not run: {e}") from None

        def body(tx: Tx, ids: _Ids) -> TaskSummary:
            run = self._load_owned(tx, ids, caller, "complete_step", step_id, claim_token)
            step = self._wf.step(run.step)
            if step.is_review:
                _check_verdict(outcome, artifacts, detail["findings"])
            elif outcome != "completed":
                raise Rejected(f"outcome must be 'completed', got {outcome!r}")
            if found[0]:  # the evidence names the wrong run, or one not finished yet
                raise Rejected("; ".join(describe(m) for m in found[0]))
            check_artifacts(step.required_artifacts, artifacts)
            for a in artifacts:
                tx.insert_artifact(ArtifactRow(run.task_id, run.id, a.name, a.path, a.commit,
                                               a.branch))
            stored = {**evidence, **({"findings": f} if (f := detail["findings"]) else {})}
            tx.update_step_run(run.id, status="completed", evidence=stored or None,
                               outcome="failed" if found[1] else outcome, closed_at=self._clock())
            if outcome == "issues":
                target = self._after_issues(tx, run.task_id, step)
            else:
                target = step.on_pass if outcome == "pass" else step.next
            if found[1]:  # the event says what the step run says: this round failed
                target, detail["outcome"] = step.on_fail, "failed"
            detail["next"] = target
            opened = self._advance(tx, run.task_id, target)
            if outcome == "issues" and target == step.on_escalate:
                ids.core_events.append(("escalate", opened, {
                    "review_step": step.name, "gate": target,
                    "issue_rounds": step.max_issue_rounds}))
            return self._summary(tx, tx.get_task(run.task_id))
        detail = {"step_id": step_id, "outcome": outcome,
                  "artifacts": [a.model_dump() for a in artifacts],
                  "findings": findings.strip() if findings and findings.strip() else None,
                  "evidence": evidence or None}
        task_id, decl = self._run(caller, "complete_step", detail, validate, writes=False)
        found = ([], []) if decl is None else self._check(caller, task_id, step_id, decl,
                                                          evidence, detail)
        return self._run(caller, "complete_step", detail, body)

    def _check(self, caller: Caller, task_id: int, step_id: int, decl: CheckDecl,
               evidence: dict[str, str], detail: dict) -> tuple[list[Mismatch], list[Mismatch]]:
        """Run the check outside any transaction, record it: (match, expect) mismatches."""
        try:
            if self._checker is None:
                raise CheckUnavailable("no checker configured")
            result = self._checker(decl.mcp_server, decl.tool, dict(decl.args))
        except CheckUnavailable as e:
            def refuse(tx: Tx, ids: _Ids) -> None:
                ids.task_id, ids.step_run_id = task_id, step_id
                raise Rejected(f"CI check could not run: {e}")
            return self._run(caller, "complete_step", detail, refuse)
        found = compare(decl.match, result), compare(decl.expect, result)
        ran = ", ".join(f"{k}={v}" for k, v in evidence.items())
        with self._store.transaction() as tx:
            self._event(tx, CORE, "check", _Ids(task_id, step_id), {
                "server": decl.mcp_server, "tool": decl.tool, "args": dict(decl.args),
                "observed": {f: lookup(result, f) for f in [*decl.match, *decl.expect]},
                "result": "mismatch" if found[0] else "fail" if found[1] else "pass",
                "summary": f"{decl.mcp_server}.{decl.tool} ({ran}): "
                           f"{'; '.join(map(describe, found[0] or found[1])) or 'pass'}"})
        return found

    def _after_issues(self, tx: Tx, task_id: int, review: Step) -> str:
        """on_escalate once this review step has max_issue_rounds `issues` verdicts since its
        last `pass`, override or the task last leaving the escalation gate; else on_issues."""
        if review.on_escalate is None:
            return review.on_issues
        runs = tx.list_step_runs(task_id)
        left = max((r.id for r in runs if r.status != "open" and (r.step == review.on_escalate
                    or (r.step == review.name and r.outcome == "pass")
                    or (r.step in (review.name, review.review_of)
                        and r.outcome == "superseded"))), default=0)
        count = sum(1 for r in runs if r.step == review.name and r.outcome == "issues"
                    and r.id > left)
        return review.on_escalate if count >= review.max_issue_rounds else review.on_issues

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


def _check_evidence(keys: tuple[str, ...], evidence: dict[str, str]) -> None:
    if set(evidence) != set(keys) or not all(isinstance(v, str) and v.strip()
                                             for v in evidence.values()):
        raise Rejected(f"evidence must be exactly: {', '.join(keys)}" if keys
                       else "this step takes no evidence")


def _check_verdict(outcome: str, artifacts: list[ArtifactIn], findings: str | None) -> None:
    if outcome not in ("pass", "issues"):
        raise Rejected(f"outcome must be 'pass' or 'issues', got {outcome!r}")
    if artifacts:
        raise Rejected("review steps take no artifacts")
    if outcome == "issues" and not findings:
        raise Rejected("findings are required for 'issues'")

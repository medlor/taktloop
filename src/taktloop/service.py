"""Operations and rules: the only place that changes state and writes history."""

from __future__ import annotations

from taktloop.models import Event, Feedback, OpenStep, StepContext, TaskDetail, TaskSummary
from taktloop.ops_base import Caller, _Ids
from taktloop.ops_gates import GateOps
from taktloop.ops_steps import StepOps
from taktloop.rules import Rejected, latest_rounds
from taktloop.store import StepRunRow, Tx

__all__ = ["Caller", "Rejected", "Service"]


class Service(StepOps, GateOps):
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
            out = []
            for run in tx.step_runs_with_status(["open"]):
                step = self._wf.step(run.step)
                if step.kind == "agent" and step.role in caller.roles:
                    out.append(OpenStep(step_id=run.id, task_id=run.task_id,
                                        task_title=tx.get_task(run.task_id).title,
                                        step=run.step, skill=step.skill, round=run.round))
            return out
        return self._run(caller, "list_open_steps", {}, body, writes=False)

    def get_step_context(self, caller: Caller, step_id: int) -> StepContext:
        def body(tx: Tx, ids: _Ids) -> StepContext:
            run = self._load(tx, ids, step_id)
            task, step = tx.get_task(run.task_id), self._wf.step(run.step)
            return StepContext(
                step_id=run.id, task_id=task.id, task_title=task.title, request=task.request,
                step=run.step, skill=step.skill or "", instructions=step.instructions,
                required_artifacts=list(step.required_artifacts), round=run.round,
                feedback=_feedback(tx, run), review_of=step.review_of,
                artifacts=latest_rounds(self._artifacts(tx, task.id), exclude_step=run.step if run.round == 1 else None))
        return self._run(caller, "get_step_context", {"step_id": step_id}, body, writes=False)

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
                history.append(Event(at=e.at, subject=e.subject, roles=e.roles,
                                     executor=e.executor, operation=e.operation,
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


def _feedback(tx: Tx, run: StepRunRow) -> Feedback | None:
    """The gate rejection or review findings whose event opened this run (it closed the run
    just before it in the task), or None."""
    before = [r for r in tx.list_step_runs(run.task_id) if r.id < run.id]
    if not before or before[-1].outcome not in ("rejected", "issues"):
        return None
    for e in tx.list_events(run.task_id):
        if e.step_run_id == before[-1].id and e.result == "ok" and e.operation in (
                "decide_gate", "complete_step"):
            gate = e.operation == "decide_gate"
            return Feedback(kind="gate_rejection" if gate else "review_issues",
                            text=e.detail["reason" if gate else "findings"], by=e.subject,
                            at=e.at)
    return None

"""Out-of-sync check: the core reads the task's ticket and flags a closed or deleted one."""

from __future__ import annotations

from taktloop.checks import CheckUnavailable, ToolFailed, compare, describe, resolve
from taktloop.models import TaskSummary
from taktloop.ops_base import CORE, Caller, ServiceBase, _Ids
from taktloop.rules import Rejected
from taktloop.store import Tx


class SyncOps(ServiceBase):
    def check_sync(self, caller: Caller, task_id: int) -> TaskSummary:
        def read(tx: Tx, ids: _Ids) -> dict:
            if (task := tx.get_task(task_id)) is None:
                raise Rejected(f"unknown task {task_id}")
            values, out = self._values(tx, task_id), {}
            for name, cfg in self._sync.items():
                try:
                    out[name] = resolve(cfg.check, {}, {}, values)
                except CheckUnavailable:
                    pass  # no ticket yet
            return out if task.status == "active" else {}

        def record(tx: Tx, ids: _Ids) -> TaskSummary:
            flagged = self._sync_flags(tx, task_id)
            for name, reason in verdicts.items():
                if reason and name not in flagged:
                    self._event(tx, CORE, "out_of_sync", ids, {"name": name, "reason": reason})
                elif not reason and name in flagged:
                    self._event(tx, CORE, "in_sync", ids, {"name": name})
            return self._summary(tx, tx.get_task(task_id))
        decls = self._run(caller, "check_sync", {}, read, writes=False, task_id=task_id)
        verdicts: dict[str, str | None] = {}
        for name, d in decls.items():
            try:
                bad = compare(d.expect, self._checker(d.mcp_server, d.tool, dict(d.args)))
                verdicts[name] = f"{name}: " + "; ".join(map(describe, bad)) if bad else None
            except CheckUnavailable as e:
                if isinstance(e, ToolFailed):
                    verdicts[name] = f"{name}: {e}"
        return self._run(caller, "check_sync", {}, record, writes=False, task_id=task_id)

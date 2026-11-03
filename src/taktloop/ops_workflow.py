"""Workflow operations: edit a draft with instant feedback, approve it as the next version."""

from __future__ import annotations

import copy

from taktloop.models import WorkflowView
from taktloop.ops_base import Caller, ServiceBase, _Ids
from taktloop.rules import Rejected
from taktloop.store import Tx
from taktloop.workflow import WorkflowError, graph_problems, parse_step, parse_workflow
from taktloop.workflow_file import data_sha, write


def _problems(data: dict) -> list[str]:
    """Everything that stops the draft from going live (its steps passed parse_step on entry)."""
    return graph_problems({n: parse_step(n, raw) for n, raw in data["steps"].items()}, data["start"])


def _view(base: int, data: dict, draft: bool) -> WorkflowView:
    return WorkflowView(version=base, draft=draft, base_version=base if draft else None,
                        data=data, problems=_problems(data) if draft else [])


class WorkflowOps(ServiceBase):
    def get_workflow(self, caller: Caller, version: int | None = None,
                     draft: bool = False) -> WorkflowView:
        def body(tx: Tx, ids: _Ids) -> WorkflowView:
            if draft:
                row = tx.get_draft()
                if row is None:
                    raise Rejected("no draft")
                return _view(row.base_version, row.data, True)
            v = self.live_version if version is None else version
            if tx.get_workflow_version(v) is None:
                raise Rejected(f"unknown version {v}")
            return _view(v, dict(self._version(tx, v).data), False)
        return self._run(caller, "get_workflow", {}, body, writes=False)

    def _edit(self, caller: Caller, op: str, detail: dict, change) -> WorkflowView:
        """Apply `change(data)` to the draft (a copy of the live version at the first edit)."""
        def body(tx: Tx, ids: _Ids) -> WorkflowView:
            row = tx.get_draft()
            base, data = (row.base_version, row.data) if row else (
                self.live_version, copy.deepcopy(dict(self._live(tx).data)))
            change(data)
            tx.put_draft(base, data, caller.subject, self._clock())
            return _view(base, data, True)
        return self._run(caller, op, detail, body)

    def put_step(self, caller: Caller, name: str, definition: dict) -> WorkflowView:
        def change(data: dict) -> None:
            try:
                step = parse_step(name, definition)
            except WorkflowError as e:
                raise Rejected(str(e)) from None
            for decl in (step.call, step.check):
                if decl and decl.mcp_server not in self._servers:
                    raise Rejected(f"step {name!r}: mcp_server {decl.mcp_server!r} is not in factory.yaml")
            data["steps"][name] = definition  # a replaced step keeps its position
        return self._edit(caller, "put_step", {"step": name}, change)

    def remove_step(self, caller: Caller, name: str) -> WorkflowView:
        def change(data: dict) -> None:
            data["steps"].pop(name, None)
        return self._edit(caller, "remove_step", {"step": name}, change)

    def set_start(self, caller: Caller, name: str) -> WorkflowView:
        return self._edit(caller, "set_start", {"step": name}, lambda data: data.update(start=name))

    def discard_draft(self, caller: Caller) -> None:
        def body(tx: Tx, ids: _Ids) -> None:
            tx.delete_draft()
        self._run(caller, "discard_draft", {}, body)

    def approve_workflow(self, caller: Caller, base_version: int) -> WorkflowView:
        with self._store.transaction() as tx:
            seen = tx.get_draft()
        refusal = None  # the tools check may call out, so it runs before the transaction opens
        if (seen and self._tools_check and "factory_owner" in caller.roles
                and seen.base_version == base_version == self.live_version
                and not _problems(seen.data)):
            refusal = self._tools_check(parse_workflow(seen.data, self.live_version + 1))

        def body(tx: Tx, ids: _Ids) -> WorkflowView:
            row = tx.get_draft()
            if row is None:
                raise Rejected("no draft")
            if not row.base_version == base_version == self.live_version:
                raise Rejected(f"draft is based on version {row.base_version}, "
                               f"live is {self.live_version}")
            if problems := _problems(row.data):
                raise Rejected(f"draft has problems: {'; '.join(problems)}")
            if seen is None or (seen.base_version, seen.data) != (row.base_version, row.data):
                raise Rejected("the draft changed while it was being checked; approve again")
            if refusal:
                raise Rejected(refusal)
            wf = parse_workflow(row.data, self.live_version + 1)
            tx.insert_workflow_version(wf.version, row.data, data_sha(row.data), "mcp",
                                       caller.subject, self._clock())
            tx.delete_draft()
            ids.core_events.append(("workflow_changed", None, {
                "version": wf.version, "source": "mcp", "by": caller.subject}))
            if self._workflow_path:
                write(self._workflow_path, row.data, wf.version)  # an error rolls everything back
            return _view(wf.version, row.data, False)
        view = self._run(caller, "approve_workflow", {"base_version": base_version}, body)
        self.live_version = view.version  # only once committed
        self._versions[view.version] = parse_workflow(view.data, view.version)
        return view

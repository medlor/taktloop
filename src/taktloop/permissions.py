"""Who may call which operation (spec 5.2)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from taktloop.rules import Rejected

if TYPE_CHECKING:
    from taktloop.ops_base import Caller
    from taktloop.workflow import Step

ANY, STEP = "any taktloop role", "the step's role"
ROLES = {
    "list_tasks": ANY, "get_task": ANY, "list_open_steps": ANY, "get_step_context": ANY,
    "create_task": "requester",
    "claim_step": STEP, "heartbeat": STEP, "complete_step": STEP, "release_step": STEP,
    "decide_gate": STEP,
    "override_review": "approver",
    "sweep": "orchestrator", "list_events": "orchestrator", "report_stall": "orchestrator",
    "release_step --force": "factory_owner",
    "get_workflow": ANY, "approve_workflow": "factory_owner",
    **dict.fromkeys(("put_step", "remove_step", "set_start", "discard_draft"),
                    ("factory_assistant", "factory_owner")),
}  # fmt: skip


def require(caller: Caller, operation: str, step: Step | None = None) -> None:
    """Raise Rejected unless the caller holds the role `operation` needs (on `step`)."""
    role = ROLES[operation]
    if role == STEP:  # without the step (the up-front check) only "has some role" is checked
        role = step.role if step else ANY
    have = ", ".join(sorted(caller.roles)) or "none"
    if role == ANY:
        if not caller.roles:
            raise Rejected(f"{operation} requires a taktloop role; you have {have}")
        return
    wanted = role if isinstance(role, tuple) else (role,)  # a tuple means any of
    if not caller.roles.intersection(wanted):
        raise Rejected(f"{operation} requires role {' or '.join(wanted)}; you have {have}")

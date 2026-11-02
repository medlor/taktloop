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
    "release_step --force": "factory_owner",
}  # fmt: skip


def require(caller: Caller, operation: str, step: Step | None = None) -> None:
    """Raise Rejected unless the caller holds the role `operation` needs (on `step`)."""
    role = ROLES[operation]
    have = ", ".join(sorted(caller.roles)) or "none"
    if role == ANY:
        if not caller.roles:
            raise Rejected(f"{operation} requires a taktloop role; you have {have}")
        return
    if role == STEP:
        role = step.role
    if role not in caller.roles:
        raise Rejected(f"{operation} requires role {role}; you have {have}")

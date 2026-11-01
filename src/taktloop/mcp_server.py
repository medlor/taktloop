"""MCP tool surface: thin wrappers over Service. No business logic here."""

from collections.abc import Callable
from functools import wraps
from typing import Literal

from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from taktloop.models import (
    ArtifactIn,
    Claim,
    OpenStepList,
    StepContext,
    TaskDetail,
    TaskList,
    TaskSummary,
)
from taktloop.service import Caller, Rejected, Service

INSTRUCTIONS = (
    "The taktloop core holds task and step state across Claude Code sessions. "
    "Use the `taktloop` skill to work on steps: claim one, do only that step, "
    "complete or release it, and never continue past a step boundary."
)


def caller_from(ctx: Context) -> Caller:
    """Map the MCP request to a Caller. This is M2's seam (auth replaces "local").

    The client's name comes from ctx.session.client_params.client_info.name,
    which is None when the client sent no clientInfo.
    """
    params = ctx.session.client_params
    executor = params.client_info.name if params is not None else None
    return Caller("local", frozenset({"*"}), executor=executor)


def _mapped(fn: Callable) -> Callable:
    """Turn service rejections into MCP tool errors carrying the reason."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Rejected as e:
            raise ToolError(e.reason) from e

    return wrapper


def build_server(service: Service) -> MCPServer:
    server = MCPServer("taktloop", instructions=INSTRUCTIONS)

    def tool(fn: Callable) -> Callable:
        return server.tool()(_mapped(fn))

    @tool
    def create_task(ctx: Context, title: str, request: str = "") -> TaskSummary:
        """Create a task; its first step opens immediately."""
        return service.create_task(caller_from(ctx), title, request)

    @tool
    def list_tasks(ctx: Context, include_closed: bool = False) -> TaskList:
        """List tasks, open ones by default."""
        return TaskList(tasks=service.list_tasks(caller_from(ctx), include_closed))

    @tool
    def get_task(ctx: Context, task_id: int) -> TaskDetail:
        """Show a task with its artifacts, history and what it is waiting for."""
        return service.get_task(caller_from(ctx), task_id)

    @tool
    def list_open_steps(ctx: Context) -> OpenStepList:
        """List steps that are open and can be claimed."""
        return OpenStepList(steps=service.list_open_steps(caller_from(ctx)))

    @tool
    def claim_step(ctx: Context, step_id: int) -> Claim:
        """Claim an open step; keep the returned claim_token for heartbeat, complete_step and release_step."""
        return service.claim_step(caller_from(ctx), step_id)

    @tool
    def get_step_context(ctx: Context, step_id: int) -> StepContext:
        """Get the instructions, request, required artifacts and prior artifacts for a step."""
        return service.get_step_context(caller_from(ctx), step_id)

    @tool
    def heartbeat(ctx: Context, step_id: int, claim_token: str) -> Claim:
        """Extend the lease on a claimed step."""
        return service.heartbeat(caller_from(ctx), step_id, claim_token)

    @tool
    def complete_step(
        ctx: Context,
        step_id: int,
        claim_token: str,
        artifacts: list[ArtifactIn],
        outcome: str = "completed",
    ) -> TaskSummary:
        """Complete a claimed step with its artifacts (repo-relative path, full commit SHA, branch)."""
        return service.complete_step(caller_from(ctx), step_id, claim_token, outcome, artifacts)

    @tool
    def release_step(
        ctx: Context,
        step_id: int,
        reason: str,
        claim_token: str | None = None,
        force: bool = False,
    ) -> TaskSummary:
        """Give a claimed step back so another session can claim it."""
        return service.release_step(caller_from(ctx), step_id, reason, claim_token, force)

    @tool
    def decide_gate(
        ctx: Context,
        task_id: int,
        decision: Literal["approve", "reject"],
        gate: str | None = None,
        reason: str | None = None,
    ) -> TaskSummary:
        """Approve or reject the human gate a task is waiting at."""
        return service.decide_gate(caller_from(ctx), task_id, gate, decision, reason)

    return server

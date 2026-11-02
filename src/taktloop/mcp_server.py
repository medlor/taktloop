"""MCP tool surface: thin wrappers over Service. No business logic here."""

from collections.abc import Callable
from functools import wraps
from typing import Literal

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from taktloop.auth import JwtVerifier, roles_from_scopes
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


def caller_from(ctx: Context, factory: str) -> Caller:
    """The Caller of the validated bearer token: subject from `sub`, roles from this
    factory's scopes, executor from the client's name (None without clientInfo)."""
    token = get_access_token()
    if token is None or not token.subject:
        raise ToolError("not authenticated")
    params = ctx.session.client_params
    executor = params.client_info.name if params is not None else None
    return Caller(token.subject, roles_from_scopes(token.scopes, factory), executor=executor)


def _mapped(fn: Callable) -> Callable:
    """Turn service rejections into MCP tool errors carrying the reason."""

    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except Rejected as e:
            raise ToolError(e.reason) from e

    return wrapper


def build_server(service: Service, factory: str, verifier: JwtVerifier | None,
                 resource_url: str | None) -> MCPServer:
    """With a verifier, the SDK refuses requests without a valid bearer token (HTTP 401).
    Without one (in-memory tests), every tool still requires a token in the auth context."""
    auth = None
    if verifier is not None:
        auth = AuthSettings(issuer_url=verifier.issuer, resource_server_url=resource_url,
                            validate_token_resource=False)
    server = MCPServer("taktloop", instructions=INSTRUCTIONS, token_verifier=verifier, auth=auth)

    def caller(ctx: Context) -> Caller:
        return caller_from(ctx, factory)

    def tool(fn: Callable) -> Callable:
        return server.tool()(_mapped(fn))

    @tool
    def create_task(ctx: Context, title: str, request: str = "") -> TaskSummary:
        """Create a task; its first step opens immediately."""
        return service.create_task(caller(ctx), title, request)

    @tool
    def list_tasks(ctx: Context, include_closed: bool = False) -> TaskList:
        """List tasks, open ones by default."""
        return TaskList(tasks=service.list_tasks(caller(ctx), include_closed))

    @tool
    def get_task(ctx: Context, task_id: int) -> TaskDetail:
        """Show a task with its artifacts, history and what it is waiting for."""
        return service.get_task(caller(ctx), task_id)

    @tool
    def list_open_steps(ctx: Context) -> OpenStepList:
        """List steps that are open and can be claimed."""
        return OpenStepList(steps=service.list_open_steps(caller(ctx)))

    @tool
    def claim_step(ctx: Context, step_id: int) -> Claim:
        """Claim an open step; keep the returned claim_token for heartbeat, complete_step and release_step."""
        return service.claim_step(caller(ctx), step_id)

    @tool
    def get_step_context(ctx: Context, step_id: int) -> StepContext:
        """Get the instructions, request, required artifacts and prior artifacts for a step."""
        return service.get_step_context(caller(ctx), step_id)

    @tool
    def heartbeat(ctx: Context, step_id: int, claim_token: str) -> Claim:
        """Extend the lease on a claimed step."""
        return service.heartbeat(caller(ctx), step_id, claim_token)

    @tool
    def complete_step(
        ctx: Context,
        step_id: int,
        claim_token: str,
        artifacts: list[ArtifactIn],
        outcome: str = "completed",
    ) -> TaskSummary:
        """Complete a claimed step with its artifacts (repo-relative path, full commit SHA, branch)."""
        return service.complete_step(caller(ctx), step_id, claim_token, outcome, artifacts)

    @tool
    def release_step(
        ctx: Context,
        step_id: int,
        reason: str,
        claim_token: str | None = None,
        force: bool = False,
    ) -> TaskSummary:
        """Give a claimed step back so another session can claim it."""
        return service.release_step(caller(ctx), step_id, reason, claim_token, force)

    @tool
    def decide_gate(
        ctx: Context,
        task_id: int,
        decision: Literal["approve", "reject"],
        gate: str | None = None,
        reason: str | None = None,
    ) -> TaskSummary:
        """Approve or reject the human gate a task is waiting at."""
        return service.decide_gate(caller(ctx), task_id, gate, decision, reason)

    return server

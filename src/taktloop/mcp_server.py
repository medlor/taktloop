"""MCP tool surface: thin wrappers over Service. No business logic here."""

from collections.abc import Callable
import inspect
from functools import wraps
from typing import Literal

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import Context, MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from pydantic import BaseModel

from taktloop.auth import JwtVerifier, roles_from_scopes
from taktloop.models import (ArtifactIn, Claim, EventFeed, FactoryStatus, OpenStepList, StepContext,
                             SweptStepList, TaskDetail, TaskList, TaskSummary, WorkflowView)
from taktloop.service import Caller, Rejected, Service

INSTRUCTIONS = (
    "The taktloop core holds task and step state across Claude Code sessions. "
    "Use the `taktloop` skill to work on steps: claim one, do only that step, "
    "complete or release it, and never continue past a step boundary."
)


def caller_from(ctx: Context, factory: str) -> Caller:
    """The token's Caller: subject `sub`, this factory's scope roles, executor the client's name (or None)."""
    token = get_access_token()
    if token is None or not token.subject:
        raise ToolError("not authenticated")
    params = ctx.session.client_params
    executor = params.client_info.name if params is not None else None
    return Caller(token.subject, roles_from_scopes(token.scopes, factory), executor=executor)


def operations(service: Service, factory: str) -> dict[str, Callable[..., BaseModel | None]]:
    """Every operation by name; each takes the Caller first, then its typed arguments."""
    ops: dict[str, Callable] = {}

    def op(fn: Callable) -> Callable:
        ops[fn.__name__] = fn
        return fn

    @op
    def create_task(caller: Caller, title: str, request: str = "", route: str | None = None) -> TaskSummary:
        """Create a task; its first step opens immediately. `route` (approver only) names a
        route of a routed agent step, which is then taken at once instead of triaged."""
        return service.create_task(caller, title, request, route)

    @op
    def list_tasks(caller: Caller, include_closed: bool = False) -> TaskList:
        """List tasks, open ones by default."""
        return TaskList(tasks=service.list_tasks(caller, include_closed))

    @op
    def get_task(caller: Caller, task_id: int) -> TaskDetail:
        """Show a task with its artifacts, history and what it is waiting for."""
        return service.get_task(caller, task_id)

    @op
    def list_open_steps(caller: Caller) -> OpenStepList:
        """List steps that are open and can be claimed."""
        return OpenStepList(steps=service.list_open_steps(caller))

    @op
    def claim_step(caller: Caller, step_id: int) -> Claim:
        """Claim an open step; keep the returned claim_token for heartbeat, complete_step and release_step."""
        return service.claim_step(caller, step_id)

    @op
    def get_step_context(caller: Caller, step_id: int) -> StepContext:
        """Get the instructions, request, required artifacts and prior artifacts for a step."""
        return service.get_step_context(caller, step_id)

    @op
    def heartbeat(caller: Caller, step_id: int, claim_token: str) -> Claim:
        """Extend the lease on a claimed step."""
        return service.heartbeat(caller, step_id, claim_token)

    @op
    def complete_step(caller: Caller, step_id: int, claim_token: str, artifacts: list[ArtifactIn] | None = None,
                      outcome: str = "completed", findings: str | None = None,
                      evidence: dict[str, str] | None = None) -> TaskSummary:
        """Complete a claimed step. Work steps: outcome 'completed' with artifacts (repo-relative
        path, full commit SHA, branch). Review steps: outcome 'pass' or 'issues', no artifacts,
        findings required for 'issues'. Steps with a check: outcome 'completed' with evidence
        (for example {"ci_run": "<run id>"}); the core checks the result itself."""
        return service.complete_step(caller, step_id, claim_token, outcome, artifacts or [],
                                     findings, evidence)

    @op
    def release_step(caller: Caller, step_id: int, reason: str, claim_token: str | None = None,
                     force: bool = False) -> TaskSummary:
        """Give a claimed step back so another session can claim it."""
        return service.release_step(caller, step_id, reason, claim_token, force)

    @op
    def decide_gate(caller: Caller, task_id: int, decision: Literal["approve", "reject", "route"],
                    gate: str | None = None, reason: str | None = None, target: str | None = None) -> TaskSummary:
        """Approve or reject the human gate a task is waiting at; a reject goes back to
        `target` (one of the gate's on_reject steps, default the first). A gate with routes
        takes only decision 'route': `target` is the route name, `reason` is required."""
        return service.decide_gate(caller, task_id, gate, decision, reason, target)

    @op
    def override_review(caller: Caller, task_id: int, reason: str,
                        review_step: str = "plan_review") -> TaskSummary:
        """Approver only: approve the latest completed round of the reviewed step regardless
        of review verdicts; the task's current run is closed as superseded."""
        return service.override_review(caller, task_id, review_step, reason)

    @op
    def sweep(caller: Caller) -> SweptStepList:
        """Orchestrator only: every open or claimed step with its idle time and lease expiries."""
        return SweptStepList(steps=service.sweep(caller))

    @op
    def list_events(caller: Caller, after_id: int, limit: int = 100) -> EventFeed:
        """Orchestrator only: successful events with id above after_id, oldest first."""
        return service.list_events(caller, after_id, limit)

    @op
    def report_stall(caller: Caller, step_id: int, reason: str) -> TaskSummary:
        """Orchestrator only: flag an open or claimed step as stalled (reported once per run)."""
        return service.report_stall(caller, step_id, reason)

    @op
    def check_sync(caller: Caller, task_id: int) -> TaskSummary:
        """Orchestrator only: read the task's ticket and flag it if closed or deleted."""
        return service.check_sync(caller, task_id)

    @op
    def get_workflow(caller: Caller, version: int | None = None, draft: bool = False) -> WorkflowView:
        """Show the live workflow (or a version, or the draft) as data with its problem list."""
        return service.get_workflow(caller, version, draft)

    @op
    def get_factory(caller: Caller) -> FactoryStatus:
        """Factory name, live workflow version, draft problems and whether each external server is available."""
        return service.get_factory(caller, factory)

    @op
    def put_step(caller: Caller, name: str, definition: dict) -> WorkflowView:
        """Add or replace a step in the workflow draft; gaps come back as problems."""
        return service.put_step(caller, name, definition)

    @op
    def remove_step(caller: Caller, name: str) -> WorkflowView:
        """Factory assistant or owner: remove a step from the workflow draft."""
        return service.remove_step(caller, name)

    @op
    def set_start(caller: Caller, name: str) -> WorkflowView:
        """Factory assistant or owner: set the draft's start step."""
        return service.set_start(caller, name)

    @op
    def discard_draft(caller: Caller) -> None:
        """Factory assistant or owner: drop the workflow draft."""
        service.discard_draft(caller)

    @op
    def approve_workflow(caller: Caller, base_version: int) -> WorkflowView:
        """Owner only: make the draft (based on `base_version`) the next live version."""
        return service.approve_workflow(caller, base_version)

    return ops


def _as_tool(fn: Callable, factory: str) -> Callable:
    """An operation as an MCP tool: `ctx: Context` instead of the Caller, rejections as tool errors."""
    sig = inspect.signature(fn)
    ctx = inspect.Parameter("ctx", inspect.Parameter.POSITIONAL_OR_KEYWORD, annotation=Context)

    @wraps(fn)
    def tool(ctx: Context, **kwargs):
        try:
            return fn(caller_from(ctx, factory), **kwargs)
        except Rejected as e:
            raise ToolError(e.reason) from e

    params = [ctx, *list(sig.parameters.values())[1:]]
    tool.__signature__ = sig.replace(parameters=params)
    tool.__annotations__ = {p.name: p.annotation for p in params} | {"return": sig.return_annotation}
    return tool


def build_server(service: Service, factory: str, verifier: JwtVerifier | None,
                 resource_url: str | None) -> MCPServer:
    """With a verifier, the SDK answers 401 without a valid bearer token; without one (in-memory
    tests), every tool still requires a token in the auth context."""
    auth = AuthSettings(issuer_url=verifier.issuer, resource_server_url=resource_url,
                        validate_token_resource=False) if verifier else None
    server = MCPServer("taktloop", instructions=INSTRUCTIONS, token_verifier=verifier, auth=auth)

    for fn in operations(service, factory).values():
        server.tool()(_as_tool(fn, factory))
    return server

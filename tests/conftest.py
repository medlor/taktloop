import socket
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
import uvicorn
import yaml
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken

from taktloop.issuer.app import build_app
from taktloop.issuer.config import load_issuer_config
from taktloop.issuer.state import init_state
from taktloop.service import Caller, Service
from taktloop.store import Store
from taktloop.workflow import parse_workflow


ALL_ROLES = ("requester", "planner", "reviewer", "approver", "factory_owner")


# --- service fixtures shared by the service, permission and separation tests ---

MVP = yaml.safe_load(
    """
start: design
steps:
  design:
    kind: agent
    role: planner
    skill: brainstorming
    instructions: >
      Run brainstorming with the requester until the design doc is written
      and the requester is happy with it. Do not invoke writing-plans.
    output:
      artifacts: [design_doc]
    next: design_approval
  design_approval:
    kind: human_gate
    role: approver
    on_approve: plan
    on_reject: design
  plan:
    kind: agent
    role: planner
    skill: writing-plans
    instructions: >
      Write the plan from the approved design doc. Do not ask for an
      execution mode and do not start executing.
    output:
      artifacts: [plan]
    next: done
"""
)
ME = Caller("local", frozenset(ALL_ROLES), "claude-code")
SHA = "a" * 40


class Clock:
    def __init__(self):
        self.now = datetime(2026, 11, 1, 14, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
def store():
    s = Store(":memory:")
    yield s
    s.close()


@pytest.fixture
def svc(store, clock):
    return Service(store, parse_workflow(MVP), clock=clock)


@pytest.fixture(autouse=True)
def _bearer_token(monkeypatch):
    """CLI calls against fake cores need some token; tests against a real core fetch real ones."""
    monkeypatch.setenv("TAKTLOOP_TOKEN", "test-token")


@contextmanager
def as_identity(subject: str, *roles: str, factory: str = "taktloop"):
    """Run in-memory `Client(build_server(...))` sessions opened inside as this validated identity."""
    scopes = [f"taktloop:{factory}:{r}" for r in roles]
    token = AccessToken(token="in-memory", client_id=subject, scopes=scopes, subject=subject)
    reset = auth_context_var.set(AuthenticatedUser(token))
    try:
        yield
    finally:
        auth_context_var.reset(reset)


@pytest.fixture
def real_issuer(tmp_path):
    """A dev issuer on a free port (uvicorn in a thread). Yields (issuer URL, credentials dir)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    (tmp_path / "dev-issuer.yaml").write_text(
        f"issuer: {url}\naudience: taktloop\ntoken_lifetime: 12h\nstate_dir: state\n"
        "identities:\n  agent-a: {scopes: [taktloop:taktloop:planner, taktloop:taktloop:reviewer]}\n"
        "  tobias: {scopes: [taktloop:taktloop:requester, taktloop:taktloop:approver,"
        " taktloop:taktloop:reviewer, taktloop:taktloop:factory_owner]}\n"
    )
    cfg = load_issuer_config(tmp_path / "dev-issuer.yaml")
    init_state(cfg, tmp_path / "creds")
    server = uvicorn.Server(uvicorn.Config(build_app(cfg), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    yield url, tmp_path / "creds"
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture
def github_runs() -> dict[str, dict]:
    """Workflow runs the fake GitHub server knows, by run id: extra `workflow_run` fields
    (head_sha, path, status, conclusion, ...) a test fills in before naming the run."""
    return {}


@pytest.fixture
def fake_github(github_runs):
    """A fake GitHub MCP server (uvicorn in a thread). Yields (URL, calls); each call is a dict
    of the tool name, its arguments and the bearer token received. Runs come from github_runs;
    an unregistered id is a completed run with no other fields, "999" is unknown (404)."""
    from mcp.server.mcpserver import MCPServer
    from mcp.server.mcpserver.exceptions import ToolError
    from starlette.middleware.base import BaseHTTPMiddleware

    calls, seen = [], []
    mcp_server = MCPServer("fake-github")

    @mcp_server.tool()
    def actions_get(method: str, owner: str, repo: str, resource_id: str) -> dict:
        if resource_id == "999":
            raise ToolError("failed to get workflow run: 404 Not Found")
        calls.append({"tool": "actions_get", "token": seen[-1], "method": method, "owner": owner,
                      "repo": repo, "resource_id": resource_id})
        return {"method": method, "workflow_run": {"id": int(resource_id), "status": "completed",
                                                   **github_runs.get(resource_id, {})}}

    @mcp_server.tool()
    def sleepy(seconds: float) -> dict:
        time.sleep(seconds)
        return {}

    class Spy(BaseHTTPMiddleware):
        async def dispatch(self, request, call_next):
            seen.append(request.headers.get("authorization", ""))
            if seen[-1] == "Bearer bad-token":
                from starlette.responses import JSONResponse
                return JSONResponse({"message": "Bad credentials"}, status_code=401)
            return await call_next(request)

    app = mcp_server.streamable_http_app()
    app.add_middleware(Spy)
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}/mcp", calls
    server.should_exit = True
    thread.join(timeout=5)

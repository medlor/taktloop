import re

import pytest
from mcp import Client, Implementation

from conftest import ALL_ROLES, as_identity
from test_service_steps import MVP, Clock  # noqa: F401
from mcp.server.auth.middleware.auth_context import auth_context_var as mcp_auth_context

from taktloop import mcp_server
from taktloop.mcp_server import build_server
from taktloop.service import Service
from taktloop.store import Store
from taktloop.workflow import parse_workflow

TOOLS = {
    "create_task", "list_tasks", "get_task", "list_open_steps", "claim_step",
    "get_step_context", "heartbeat", "complete_step", "release_step", "decide_gate",
}  # fmt: skip

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture
def client_for():
    store = Store(":memory:")
    svc = Service(store, parse_workflow(MVP), clock=Clock())

    def make():
        return Client(build_server(svc, "taktloop", None, None),
                      client_info=Implementation(name="claude-code", version="0"))

    with as_identity("local", *ALL_ROLES):
        yield make
    store.close()


async def test_lists_exactly_the_m1_tools(client_for):
    async with client_for() as c:
        assert {t.name for t in (await c.list_tools()).tools} == TOOLS


async def test_create_and_claim_structured(client_for):
    async with client_for() as c:
        r = await c.call_tool("create_task", {"title": "T"})
        assert r.structured_content["current_step"] == "design"
        r = await c.call_tool("claim_step", {"step_id": r.structured_content["step_id"]})
        assert re.fullmatch(r"[0-9a-f]{32}", r.structured_content["claim_token"])


async def test_rejection_is_tool_error_with_reason(client_for):
    async with client_for() as c:
        t = await c.call_tool("create_task", {"title": "T"})
        sid = t.structured_content["step_id"]
        await c.call_tool("claim_step", {"step_id": sid})
        r = await c.call_tool("claim_step", {"step_id": sid})
        assert r.is_error
        assert "already claimed" in r.content[0].text


async def test_executor_name_reaches_waiting_for(client_for):
    async with client_for() as c:
        t = await c.call_tool("create_task", {"title": "T"})
        await c.call_tool("claim_step", {"step_id": t.structured_content["step_id"]})
        r = await c.call_tool("get_task", {"task_id": t.structured_content["id"]})
        assert "local (claude-code)" in r.structured_content["waiting_for"]


async def test_no_token_is_not_authenticated(client_for):
    token = mcp_auth_context.set(None)
    try:
        async with client_for() as c:
            r = await c.call_tool("list_tasks", {})
    finally:
        mcp_auth_context.reset(token)
    assert r.is_error and r.content[0].text.endswith("not authenticated")


async def test_subject_from_token_reaches_history(client_for):
    with as_identity("agent-a", "requester", "planner"):
        async with client_for() as c:
            t = await c.call_tool("create_task", {"title": "T"})
            await c.call_tool("claim_step", {"step_id": t.structured_content["step_id"]})
            r = await c.call_tool("get_task", {"task_id": t.structured_content["id"]})
    assert "agent-a (claude-code)" in r.structured_content["waiting_for"]
    assert any((e["subject"], e["roles"], e["executor"]) == ("agent-a", ["planner", "requester"], "claude-code")
               for e in r.structured_content["history"])


async def test_roles_come_from_this_factorys_scopes(monkeypatch, client_for):
    seen = []
    monkeypatch.setattr(mcp_server.Service, "list_tasks", lambda self, caller, inc: seen.append(caller) or [])
    with as_identity("agent-a", "planner", factory="other"):
        async with client_for() as c:
            await c.call_tool("list_tasks", {})
    with as_identity("agent-b", "planner", "reviewer"):
        async with client_for() as c:
            await c.call_tool("list_tasks", {})
    assert [(x.subject, x.roles) for x in seen] == [
        ("agent-a", frozenset()), ("agent-b", frozenset({"planner", "reviewer"}))]


async def test_list_results_are_wrapped(client_for):
    async with client_for() as c:
        await c.call_tool("create_task", {"title": "T"})
        assert len((await c.call_tool("list_tasks", {})).structured_content["tasks"]) == 1
        assert len((await c.call_tool("list_open_steps", {})).structured_content["steps"]) == 1

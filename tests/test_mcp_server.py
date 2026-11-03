import re

import pytest
from mcp import Client, Implementation

from conftest import ALL_ROLES, MVP, Clock, as_identity
from mcp.server.auth.middleware.auth_context import auth_context_var as mcp_auth_context

from taktloop import mcp_server
from taktloop.mcp_server import build_server
from taktloop.service import Service
from taktloop.store import Store
from taktloop.workflow import parse_workflow

TOOLS = {
    "create_task", "list_tasks", "get_task", "list_open_steps", "claim_step",
    "get_step_context", "heartbeat", "complete_step", "release_step", "decide_gate",
    "override_review", "sweep", "report_stall", "list_events", "get_workflow", "put_step",
    "remove_step", "set_start", "discard_draft", "approve_workflow",
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


async def test_lists_exactly_the_tools(client_for):
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


async def test_override_review_tool_defaults_to_plan_review(client_for):
    async with client_for() as c:
        t = await c.call_tool("create_task", {"title": "T"})
        r = await c.call_tool("override_review", {"task_id": t.structured_content["id"],
                                                  "reason": "skip"})
    assert r.is_error
    assert r.content[0].text.endswith("plan_review is not a review step")  # the default step


async def test_complete_step_passes_evidence_to_the_service(monkeypatch, client_for):
    seen = []
    monkeypatch.setattr(mcp_server.Service, "complete_step",
                        lambda self, *a: seen.append(a) or None)
    async with client_for() as c:
        await c.call_tool("complete_step", {"step_id": 1, "claim_token": "t", "outcome": "completed",
                                            "evidence": {"ci_run": "12345"}})
    assert seen[0][1:] == (1, "t", "completed", [], None, {"ci_run": "12345"})


async def test_sweep_under_orchestrator_role(client_for):
    async with client_for() as c:
        await c.call_tool("create_task", {"title": "T"})
    with as_identity("orchestrator", "orchestrator"):
        async with client_for() as c:
            r = await c.call_tool("sweep", {})
    assert [(s["step"], s["status"]) for s in r.structured_content["steps"]] == [("design", "open")]


async def test_workflow_tools_round_trip(client_for):
    async with client_for() as c:
        step = {"kind": "agent", "role": "b", "skill": "s", "output": {"artifacts": ["x"]}, "next": "nowhere"}
        r = await c.call_tool("put_step", {"name": "lint", "definition": step})
        assert r.structured_content["draft"] and r.structured_content["problems"]
        bad = await c.call_tool("put_step", {"name": "x", "definition": {"kind": "teleport", "role": "r"}})
        assert bad.is_error
        await c.call_tool("discard_draft", {})
        assert (await c.call_tool("get_workflow", {"draft": True})).is_error
        assert (await c.call_tool("get_workflow", {})).structured_content["version"] == 1

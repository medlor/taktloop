import pytest
from mcp.server.auth.provider import AccessToken
from starlette.applications import Starlette
from starlette.testclient import TestClient

from conftest import ALL_ROLES, MVP, Clock
from taktloop.http_api import routes
from taktloop.mcp_server import operations
from taktloop.service import Service
from taktloop.store import Store
from taktloop.workflow import parse_workflow


class FakeVerifier:
    async def verify_token(self, token):
        if token == "good":
            return AccessToken(token=token, client_id="local", subject="local",
                               scopes=[f"taktloop:taktloop:{r}" for r in ALL_ROLES])


@pytest.fixture
def api():
    store = Store(":memory:")
    svc = Service(store, parse_workflow(MVP), clock=Clock())
    app = Starlette(routes=routes(operations(svc, "taktloop"), "taktloop", FakeVerifier()))
    yield lambda tool, body=None, token="good": TestClient(app).post(
        f"/api/{tool}", json=body or {}, headers={"Authorization": f"Bearer {token}"} if token else {})
    store.close()


def test_create_and_get_task(api):
    r = api("create_task", {"title": "T"})
    assert r.status_code == 200 and r.json()["status"] == "active"
    assert api("get_task", {"task_id": r.json()["id"]}).json()["title"] == "T"


def test_rejected_is_409_with_reason(api):
    tid = api("create_task", {"title": "T"}).json()["id"]
    r = api("decide_gate", {"task_id": tid, "decision": "approve"})
    assert r.status_code == 409 and r.json()["reason"]


def test_no_token_401(api):
    assert api("list_tasks", token=None).status_code == 401
    r = api("list_tasks", token="bad")
    assert r.status_code == 401 and r.json() == {"reason": "not authenticated"}


def test_unknown_operation_404(api):
    r = api("nope")
    assert r.status_code == 404 and r.json() == {"reason": "unknown operation nope"}


def test_bad_arguments_422(api):
    assert api("get_task", {"task_id": "x"}).status_code == 422
    assert api("get_task", {}).status_code == 422


def test_validation_reason_names_the_field(api):
    reason = api("get_task", {"task_id": "x"}).json()["reason"]
    assert "task_id" in reason and "operations" not in reason
    assert api("get_task", {"task_id": 1, "bogus": 1}).json()["reason"].startswith("bogus")


def test_bad_json_and_non_object_body_422(api):
    client = TestClient(Starlette(routes=routes({"list_tasks": lambda caller: None}, "taktloop", FakeVerifier())))
    h = {"Authorization": "Bearer good"}
    assert client.post("/api/list_tasks", content=b"{", headers=h).json() == {"reason": "body is not valid JSON"}
    assert client.post("/api/list_tasks", json=[1], headers=h).json() == {"reason": "body must be a JSON object"}


def test_service_bug_is_500_not_422():
    def boom(caller):
        raise ValueError("internal")
    client = TestClient(Starlette(routes=routes({"boom": boom}, "taktloop", FakeVerifier())),
                        raise_server_exceptions=False)
    assert client.post("/api/boom", headers={"Authorization": "Bearer good"}).status_code == 500


def test_operation_runs_off_the_event_loop():
    import anyio
    from taktloop.models import TaskList

    async def nothing():
        return None

    def probe(caller):
        anyio.run(nothing)  # what checks do: fails on the event loop thread
        return TaskList(tasks=[])
    client = TestClient(Starlette(routes=routes({"probe": probe}, "taktloop", FakeVerifier())))
    assert client.post("/api/probe", headers={"Authorization": "Bearer good"}).status_code == 200

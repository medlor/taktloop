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

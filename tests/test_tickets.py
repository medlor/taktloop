import anyio
import pytest
from mcp import Client, Implementation

from taktloop.tickets import StaticTokenVerifier, TicketStore, build_tickets_server


def test_store_lifecycle(tmp_path):
    s = TicketStore(tmp_path / "t.db")
    t = s.create("Add X", "body")
    assert (t["id"], t["status"], t["closed"]) == (1, "open", False)
    assert s.update_status(1, "in design")["status"] == "in design"
    assert s.comment(1, "hello")["comments"][0]["text"] == "hello"
    c = s.close(1, "done")
    assert (c["status"], c["closed"]) == ("closed", True)
    with pytest.raises(ValueError):
        s.update_status(1, "planning")
    assert s.list() == [] and len(s.list(include_closed=True)) == 1
    assert s.reopen(1)["closed"] is False
    assert s.delete(1) == {"deleted": 1}
    with pytest.raises(KeyError):
        s.get(1)


def test_static_token_verifier():
    v = StaticTokenVerifier("secret")
    assert anyio.run(v.verify_token, "secret") is not None
    assert anyio.run(v.verify_token, "nope") is None


def test_server_tools_in_memory(tmp_path):
    store = TicketStore(tmp_path / "t.db")

    async def scenario():
        client = Client(build_tickets_server(store, None, "http://x/mcp"),
                        client_info=Implementation(name="t", version="0"))
        async with client as c:
            made = await c.call_tool("create_ticket", {"title": "A"})
            assert made.structured_content["id"] == 1
            got = await c.call_tool("get_ticket", {"id": "1"})
            assert got.structured_content["title"] == "A"
            listed = await c.call_tool("list_tickets", {})
            assert [t["id"] for t in listed.structured_content["tickets"]] == [1]
            missing = await c.call_tool("get_ticket", {"id": 9})
            assert missing.is_error and "unknown ticket 9" in missing.content[0].text
            await c.call_tool("close_ticket", {"id": 1})
            refused = await c.call_tool("update_status", {"id": 1, "status": "x"})
            assert refused.is_error

    anyio.run(scenario)


def test_status_invariants(tmp_path):
    s = TicketStore(tmp_path / "t.db")
    s.create("A")
    with pytest.raises(ValueError):
        s.update_status(1, "closed")
    with pytest.raises(ValueError):
        s.reopen(1)
    assert s.update_status(1, "in design")["status"] == "in design"
    with pytest.raises(KeyError):
        s.delete(9)
    with pytest.raises(KeyError):
        s.close(9)


def test_bearer_auth_on_http(tmp_path):
    from starlette.testclient import TestClient

    app = build_tickets_server(TicketStore(tmp_path / "t.db"), "tok", "http://t/mcp").streamable_http_app(host="t")
    init = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
        "protocolVersion": "2025-03-26", "capabilities": {}, "clientInfo": {"name": "t", "version": "0"}}}
    hdr = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    with TestClient(app, base_url="http://t") as c:
        assert c.post("/mcp", json=init, headers=hdr).status_code == 401
        assert c.post("/mcp", json=init, headers={**hdr, "Authorization": "Bearer bad"}).status_code == 401
        assert c.post("/mcp", json=init, headers={**hdr, "Authorization": "Bearer tok"}).status_code == 200


def test_tickets_token_lookup(tmp_path, monkeypatch):
    from taktloop.cli_tickets import _tickets_token

    env = tmp_path / ".env"
    monkeypatch.delenv("TK_X", raising=False)
    with pytest.raises(ValueError):
        _tickets_token("TK_X", env)
    env.write_text("TK_X=from-file\n")
    assert _tickets_token("TK_X", env) == "from-file"
    monkeypatch.setenv("TK_X", "from-env")
    assert _tickets_token("TK_X", env) == "from-env"

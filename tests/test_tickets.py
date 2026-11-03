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

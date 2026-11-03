"""The ticket stand-in: a tiny Jira-like MCP server on its own SQLite file (knows nothing about tasks)."""

import hmac
import sqlite3
import threading
from datetime import UTC, datetime
from functools import wraps
from pathlib import Path
from typing import Any

from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

SCHEMA = """
CREATE TABLE IF NOT EXISTS tickets(id INTEGER PRIMARY KEY AUTOINCREMENT, title TEXT NOT NULL,
  body TEXT NOT NULL, status TEXT NOT NULL, closed INTEGER NOT NULL, created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS comments(ticket_id INTEGER NOT NULL, text TEXT NOT NULL, at TEXT NOT NULL);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


class TicketStore:
    def __init__(self, path: Path | str):
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(SCHEMA)

    def _ticket(self, id: int) -> dict:
        row = self._conn.execute("SELECT * FROM tickets WHERE id=?", (id,)).fetchone()
        if row is None:
            raise KeyError(id)
        comments = self._conn.execute(
            "SELECT text, at FROM comments WHERE ticket_id=? ORDER BY rowid", (id,)).fetchall()
        return {"id": row["id"], "title": row["title"], "body": row["body"], "status": row["status"],
                "closed": bool(row["closed"]), "comments": [dict(c) for c in comments]}

    def _set(self, id: int, status: str, closed: bool) -> dict:
        self._ticket(id)
        self._conn.execute("UPDATE tickets SET status=?, closed=?, updated_at=? WHERE id=?",
                           (status, closed, _now(), id))
        return self._ticket(id)

    def create(self, title: str, body: str = "") -> dict:
        with self._lock:
            now = _now()
            cur = self._conn.execute(
                "INSERT INTO tickets(title, body, status, closed, created_at, updated_at) "
                "VALUES(?,?,'open',0,?,?)", (title, body, now, now))
            return self._ticket(cur.lastrowid)

    def get(self, id: int) -> dict:
        with self._lock:
            return self._ticket(id)

    def list(self, include_closed: bool = False) -> list[dict]:
        with self._lock:
            ids = self._conn.execute("SELECT id FROM tickets WHERE closed=0 OR ? ORDER BY id",
                                     (include_closed,)).fetchall()
            return [self._ticket(r["id"]) for r in ids]

    def update_status(self, id: int, status: str) -> dict:
        with self._lock:
            if self._ticket(id)["closed"]:
                raise ValueError(f"ticket {id} is closed")
            return self._set(id, status, False)

    def comment(self, id: int, text: str) -> dict:
        with self._lock:
            self._ticket(id)
            self._conn.execute("INSERT INTO comments VALUES(?,?,?)", (id, text, _now()))
            return self._ticket(id)

    def close(self, id: int, resolution: str = "") -> dict:
        with self._lock:
            self._ticket(id)
            if resolution:
                self._conn.execute("INSERT INTO comments VALUES(?,?,?)", (id, resolution, _now()))
            return self._set(id, "closed", True)

    def reopen(self, id: int) -> dict:
        with self._lock:
            return self._set(id, "open", False)

    def delete(self, id: int) -> dict:
        with self._lock:
            self._ticket(id)
            self._conn.execute("DELETE FROM comments WHERE ticket_id=?", (id,))
            self._conn.execute("DELETE FROM tickets WHERE id=?", (id,))
            return {"deleted": id}


class StaticTokenVerifier:
    """mcp's TokenVerifier for one shared secret, like a Jira API token."""

    def __init__(self, token: str):
        self._token = token.encode()

    async def verify_token(self, token: str) -> AccessToken | None:
        if not hmac.compare_digest(token.encode(), self._token):
            return None
        return AccessToken(token="static", client_id="tickets", scopes=[])


def _mapped(fn):
    @wraps(fn)
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except KeyError as e:
            raise ToolError(f"unknown ticket {e.args[0]}") from e
        except ValueError as e:
            raise ToolError(str(e)) from e

    return wrapper


def build_tickets_server(store: TicketStore, token: str | None, url: str) -> MCPServer:
    """`token=None` disables authentication (tests only)."""
    verifier = StaticTokenVerifier(token) if token else None
    auth = AuthSettings(issuer_url=url, resource_server_url=url, validate_token_resource=False) if verifier else None
    server = MCPServer("taktloop-tickets", token_verifier=verifier, auth=auth)

    def tool(fn):
        return server.tool()(_mapped(fn))

    @tool
    def create_ticket(title: str, body: str = "") -> dict[str, Any]:
        """Create a ticket (status "open")."""
        return store.create(title, body)

    @tool
    def get_ticket(id: int) -> dict[str, Any]:
        """Show a ticket with its comments."""
        return store.get(id)

    @tool
    def list_tickets(include_closed: bool = False) -> dict[str, Any]:
        """List tickets, open ones by default."""
        return {"tickets": store.list(include_closed)}

    @tool
    def update_status(id: int, status: str) -> dict[str, Any]:
        """Set the free-text status of an open ticket."""
        return store.update_status(id, status)

    @tool
    def comment(id: int, text: str) -> dict[str, Any]:
        """Add a comment to a ticket."""
        return store.comment(id, text)

    @tool
    def close_ticket(id: int, resolution: str = "") -> dict[str, Any]:
        """Close a ticket; a resolution is recorded as a comment."""
        return store.close(id, resolution)

    @tool
    def reopen_ticket(id: int) -> dict[str, Any]:
        """Reopen a closed ticket (status "open")."""
        return store.reopen(id)

    @tool
    def delete_ticket(id: int) -> dict[str, Any]:
        """Delete a ticket and its comments."""
        return store.delete(id)

    return server


def run(store: TicketStore, token: str, port: int) -> None:
    import uvicorn

    from taktloop.serve import _Server

    url = f"http://127.0.0.1:{port}/mcp"
    app = build_tickets_server(store, token, url).streamable_http_app(host="127.0.0.1")
    print(f"taktloop ticket stand-in on {url}", flush=True)
    try:
        _Server(uvicorn.Config(app, host="127.0.0.1", port=port, timeout_graceful_shutdown=3)).run()
    except KeyboardInterrupt:
        pass

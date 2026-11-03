"""`taktloop tickets`: run the stand-in, or manage tickets as an MCP client of it."""

import json
import os
import sys
from pathlib import Path

import anyio
import yaml

from taktloop.cli_commands import CoreUnreachable, TokenRejected, ToolRejected, _call_tool
from taktloop.config import load_config
from taktloop.external import read_dotenv

TOOLS = {"list": "list_tickets", "show": "get_ticket", "close": "close_ticket",
         "reopen": "reopen_ticket", "delete": "delete_ticket"}


def _tickets_token(name: str, dotenv: Path) -> str:
    if token := os.environ.get(name) or read_dotenv(dotenv).get(name):
        return token
    raise ValueError(f"{name} is not set: put it in the environment or in {dotenv}")


def _tickets(ns) -> int:
    try:
        if ns.sub == "serve":
            from taktloop import tickets  # the server stack; client commands never load it

            token = _tickets_token("TAKTLOOP_TICKETS_TOKEN", Path(".env"))
            ns.database.parent.mkdir(parents=True, exist_ok=True)
            tickets.run(tickets.TicketStore(ns.database), token, ns.port)
            return 0
        server = load_config(ns.config).mcp_servers.get("tickets")
        if server is None:
            raise ValueError(f"{ns.config.name} has no mcp_servers.tickets")
        args = {"include_closed": ns.all} if ns.sub == "list" else {"id": ns.id}
        if ns.sub == "close":
            args["resolution"] = ns.resolution
        token = _tickets_token(server.token_env, ns.config.parent / ".env")
        print(json.dumps(anyio.run(lambda: _call_tool(server.url, TOOLS[ns.sub], args, token,
                                                      client="taktloop-tickets-cli")), indent=2))
    except ToolRejected as e:
        print(f"rejected: {e}", file=sys.stderr)
        return 1
    except CoreUnreachable:
        print(f"ticket stand-in not reachable at {server.url} — start it with `uv run taktloop tickets serve`",
              file=sys.stderr)
        return 2
    except TokenRejected:
        print("taktloop: the stand-in refused the token (401)", file=sys.stderr)
        return 2
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as e:
        print(f"taktloop: {e}", file=sys.stderr)
        return 2
    return 0

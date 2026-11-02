"""Call external MCP servers (GitHub's hosted one) with the core's own read-only credential."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

import anyio
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from taktloop.checks import CheckUnavailable, read_result

Checker = Callable[[str, str, dict[str, str]], dict]  # (server name, tool, args) -> result


@dataclass(frozen=True)
class ExternalServer:
    name: str
    url: str
    token_env: str


def read_dotenv(path: Path) -> dict[str, str]:
    values = {}
    for line in path.read_text().splitlines() if path.exists() else []:
        key, eq, value = line.strip().removeprefix("export ").partition("=")
        if key.startswith("#") or not eq:
            continue
        if len(value) > 1 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def credential(server: ExternalServer, environ: Mapping[str, str], dotenv: Mapping[str, str]) -> str | None:
    return environ.get(server.token_env) or dotenv.get(server.token_env) or None


def make_checker(servers: Mapping[str, ExternalServer], environ: Mapping[str, str],
                 dotenv: Mapping[str, str], timeout: float = 30.0) -> Checker:
    async def call(server: ExternalServer, token: str, tool: str, args: dict) -> dict:
        with anyio.fail_after(timeout):
            async with create_mcp_http_client(headers={"Authorization": f"Bearer {token}"}) as http, \
                    Client(streamable_http_client(server.url, http_client=http)) as client:
                result = await client.call_tool(tool, args)
        if result.is_error:
            raise CheckUnavailable(result.content[0].text if result.content else "unknown error")
        return read_result(result.structured_content, [c.text for c in result.content])

    def check(name: str, tool: str, args: dict[str, str]) -> dict:
        server = servers[name]
        token = credential(server, environ, dotenv)
        if token is None:
            raise CheckUnavailable(f"the core has no credential for {name} ({server.token_env} is not set)")
        try:
            return anyio.run(call, server, token, tool, args)
        except CheckUnavailable:
            raise
        except TimeoutError:
            raise CheckUnavailable(f"{name} did not answer within {timeout:g} s") from None
        except Exception as e:  # no message text: it could echo the Authorization header
            raise CheckUnavailable(f"{name} is unreachable: {type(e).__name__}") from None

    return check

"""Call external MCP servers (GitHub's hosted one) with the core's own read-only credential."""

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import anyio
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from taktloop.checks import CheckUnavailable, ToolFailed, read_result

Checker = Callable[[str, str, dict[str, Any]], dict]  # (server name, tool, args) -> result


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
        value = value.strip()
        if len(value) > 1 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def credential(server: ExternalServer, environ: Mapping[str, str], dotenv: Mapping[str, str]) -> str | None:
    return environ.get(server.token_env) or dotenv.get(server.token_env) or None


def _remote(servers: Mapping[str, ExternalServer], environ: Mapping[str, str],
            dotenv: Mapping[str, str], timeout: float) -> Callable[[str, Callable], Any]:
    """`go(name, action)`: run `await action(client)` against server `name`; failures are CheckUnavailable."""
    async def call(server: ExternalServer, token: str, action: Callable, refused: list[int]) -> Any:
        async def note(response) -> None:  # the SDK reports an HTTP error status without its code
            if response.status_code >= 400:
                refused.append(response.status_code)

        with anyio.fail_after(timeout):
            async with create_mcp_http_client(headers={"Authorization": f"Bearer {token}"}) as http:
                http.event_hooks["response"].append(note)
                async with Client(streamable_http_client(server.url, http_client=http)) as client:
                    return await action(client)

    def go(name: str, action: Callable) -> Any:
        if name not in servers:
            raise CheckUnavailable(f"{name} is not configured in factory.yaml")
        server = servers[name]
        token = credential(server, environ, dotenv)
        if token is None:
            raise CheckUnavailable(f"the core has no credential for {name} ({server.token_env} is not set)")
        refused: list[int] = []
        try:
            return anyio.run(call, server, token, action, refused)
        except CheckUnavailable:
            raise
        except TimeoutError:
            raise CheckUnavailable(f"{name} did not answer within {timeout:g} s") from None
        except Exception as e:  # no message text: it could echo the Authorization header
            while isinstance(e, BaseExceptionGroup) and len(e.exceptions) == 1:
                e = e.exceptions[0]
            if refused:
                raise CheckUnavailable(f"{name} answered {refused[0]}") from None
            raise CheckUnavailable(f"{name} is unreachable: {type(e).__name__}") from None

    return go


def make_checker(servers: Mapping[str, ExternalServer], environ: Mapping[str, str],
                 dotenv: Mapping[str, str], timeout: float = 30.0) -> Checker:
    go = _remote(servers, environ, dotenv, timeout)

    def check(name: str, tool: str, args: dict[str, str]) -> dict:
        result = go(name, lambda client: client.call_tool(tool, args))
        if result.is_error:  # the first text block; image, audio and resource blocks have none
            raise ToolFailed(next((t for c in result.content if (t := getattr(c, "text", ""))), "unknown error"))
        return read_result(result.structured_content, [getattr(c, "text", "") for c in result.content])

    return check


def make_tool_lister(servers: Mapping[str, ExternalServer], environ: Mapping[str, str],
                     dotenv: Mapping[str, str], timeout: float = 30.0) -> Callable[[str], set[str]]:
    go = _remote(servers, environ, dotenv, timeout)

    async def names(client) -> set[str]:
        return {t.name for t in (await client.list_tools()).tools}

    return lambda name: go(name, names)

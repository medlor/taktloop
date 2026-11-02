"""Command handlers for the ``taktloop`` CLI: client calls, serve, issuer, token (rendering in cli_render)."""

import json
import os
import re
import shutil
import socket
import sys
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlsplit

import anyio
import yaml
from mcp import Client, Implementation
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from taktloop.cli_render import render_steps, render_summary, render_task_detail, render_task_list
from taktloop.token_client import TokenError, fetch_token

TEMPLATES = Path(__file__).resolve().parents[2] / "templates"


class ToolRejected(Exception):
    """The core refused the operation; the message is the reason."""


class CoreUnreachable(Exception):
    """No core is listening at the URL."""


class TokenRejected(Exception):
    """The core answered 401: the bearer token is missing, invalid or expired."""


def _only_os_errors(e: BaseException) -> bool:
    if isinstance(e, BaseExceptionGroup):
        return all(_only_os_errors(s) for s in e.exceptions)
    return isinstance(e, OSError)


def _valid_url(url: str) -> bool:
    try:
        parts = urlsplit(url)
        parts.port  # noqa: B018 - raises ValueError when malformed
    except ValueError:
        return False
    return parts.scheme in ("http", "https") and bool(parts.hostname)


def _probe(url: str) -> None:
    """Cheap stdlib reachability check, so a down core is reported without transport internals."""
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        socket.create_connection((parts.hostname, port), timeout=2).close()
    except OSError:
        raise CoreUnreachable(url) from None


_TOOL_PREFIX = re.compile(r"^Error executing tool \S+: ")


def _reason(text: str) -> str:
    return _TOOL_PREFIX.sub("", text, count=1)


async def _call_tool(url: str, name: str, args: dict, token: str) -> dict:
    _probe(url)
    info = Implementation(name="taktloop-cli", version=version("taktloop"))
    http = create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})
    unauthorized = []

    async def note_401(response):  # the SDK turns a 401 into a generic error; keep the status
        if response.status_code == 401:
            unauthorized.append(response)

    http.event_hooks["response"].append(note_401)
    try:
        async with Client(streamable_http_client(url, http_client=http), client_info=info) as client:
            result = await client.call_tool(name, args)
    except BaseException as e:
        if unauthorized:
            raise TokenRejected from None
        if _only_os_errors(e):
            raise CoreUnreachable(url) from None
        raise
    if result.is_error:
        raise ToolRejected(_reason(result.content[0].text) if result.content else "unknown error")
    return result.structured_content


def _request(ns) -> tuple[str, dict, object]:
    """Map parsed args to (tool, arguments, renderer)."""
    if ns.cmd == "task":
        if ns.sub == "create":
            text = sys.stdin.read() if ns.request == "-" else ns.request
            return "create_task", {"title": ns.title, "request": text}, render_summary
        if ns.sub == "list":
            return "list_tasks", {"include_closed": ns.all}, render_task_list
        return "get_task", {"task_id": ns.id}, render_task_detail
    if ns.cmd in ("approve", "reject"):
        args = {"task_id": ns.task, "decision": ns.cmd, "gate": ns.gate}
        if ns.cmd == "reject":
            args["reason"], args["target"] = ns.reason, ns.to
        return "decide_gate", args, render_summary
    if ns.cmd == "override":
        args = {"task_id": ns.task, "reason": ns.reason, "review_step": ns.step}
        return "override_review", args, render_summary
    if ns.cmd == "review":
        return "complete_step", {}, render_summary  # several calls: see _review
    if ns.cmd == "release":
        args = {"step_id": ns.step_id, "reason": ns.reason, "force": True}
        return "release_step", args, render_summary
    return "list_open_steps", {}, render_steps


async def _review(url: str, ns, token: str) -> dict:
    """`taktloop review`: claim the task's current review step and complete it with the verdict."""
    task = await _call_tool(url, "get_task", {"task_id": ns.task}, token)
    if task["current_step"] is None:
        raise ToolRejected(f"task {ns.task} has no open step ({task['status']})")
    ctx = task["step_id"] and await _call_tool(url, "get_step_context", {"step_id": task["step_id"]}, token)
    if not (ctx and ctx.get("review_of")):
        raise ToolRejected(f"task {ns.task} is at {task['current_step']}, not at a review step")
    claim = await _call_tool(url, "claim_step", {"step_id": task["step_id"]}, token)
    args = {"step_id": claim["step_id"], "claim_token": claim["claim_token"],
            "outcome": "pass" if ns.passed else "issues", "findings": ns.issues}
    try:
        return await _call_tool(url, "complete_step", args, token)
    except ToolRejected:  # don't leave the step claimed until the lease runs out
        release = {"step_id": claim["step_id"], "claim_token": claim["claim_token"],
                   "reason": "taktloop review: verdict rejected"}
        await _call_tool(url, "release_step", release, token)
        raise


def _serve(cfg) -> int:
    from taktloop import serve  # the server stack; client commands never load it
    from taktloop.store import SchemaMismatch

    try:
        serve.run(cfg)
    except SchemaMismatch as e:  # an M1 database: tell the user to delete it, no traceback
        print(f"taktloop: {e}", file=sys.stderr)
        return 2
    return 0


def _install_skill(dest: Path) -> int:
    """Copy both skills (the bridge and taktloop-plan-review) into dest, one directory each."""
    shutil.copytree(TEMPLATES / "mvp" / "skills", dest, dirs_exist_ok=True)
    print(dest)
    return 0


def _issuer(ns) -> int:
    from taktloop.issuer.app import build_app  # only the issuer command loads it
    from taktloop.issuer.config import load_issuer_config
    from taktloop.issuer.state import init_state

    try:
        cfg = load_issuer_config(ns.config)
        if ns.sub == "init":
            creds = ns.credentials or ns.config.parent / ".taktloop/credentials"
            written = init_state(cfg, creds, ns.rotate)
            print("\n".join(f"{i}: {creds / (i + '.json')}" for i in written) or "nothing to do")
            return 0
        app = build_app(cfg)
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as e:
        print(f"taktloop: {e}", file=sys.stderr)
        return 2
    import uvicorn

    print("WARNING: taktloop dev issuer, for development only", file=sys.stderr)
    uvicorn.run(app, host=cfg.host, port=cfg.port)
    return 0


def _token(ns) -> int:
    try:
        print(fetch_token(ns.identity, ns.credentials or ns.config.parent / ".taktloop/credentials"))
    except TokenError as e:
        print(f"taktloop: {e}", file=sys.stderr)
        return 2
    return 0


def _bearer(ns, cfg) -> str:
    """TAKTLOOP_TOKEN, else a fresh token for --as, else for cli.identity from the config."""
    if token := os.environ.get("TAKTLOOP_TOKEN"):
        return token
    identity = ns.identity or (cfg.cli_identity if cfg else None)
    if not identity:
        raise TokenError("no identity: set TAKTLOOP_TOKEN, pass --as, or set cli.identity in factory.yaml")
    return fetch_token(identity, cfg.credentials if cfg else Path(".taktloop/credentials"))


def run_client(ns, cfg) -> int:
    url = ns.url or cfg.url
    if not _valid_url(url):
        print(f"taktloop: invalid URL {url}", file=sys.stderr)
        return 2
    tool, args, render = _request(ns)
    try:
        call = (_review, url, ns) if ns.cmd == "review" else (_call_tool, url, tool, args)
        result = anyio.run(*call, _bearer(ns, cfg))
    except ToolRejected as e:
        print(f"rejected: {e}", file=sys.stderr)
        return 1
    except TokenRejected:
        print("taktloop: token missing or expired; get a new one with taktloop token <identity>", file=sys.stderr)
        return 2
    except CoreUnreachable:
        print(f"taktloop core not reachable at {url} — start it with `uv run taktloop serve`", file=sys.stderr)
        return 2
    except TokenError as e:
        print(f"taktloop: {e}", file=sys.stderr)
        return 2
    except Exception as e:
        while isinstance(e, BaseExceptionGroup):
            e = e.exceptions[0]
        msg = " ".join(f"{type(e).__name__}: {e}".split())
        print(f"taktloop: {msg}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2) if ns.json else render(result))
    return 0

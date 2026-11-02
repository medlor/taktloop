"""Command handlers for the ``taktloop`` CLI: client calls, rendering, serve, issuer, token."""

import json
import os
import re
import shutil
import socket
import sys
from datetime import UTC, datetime
from importlib.metadata import version
from pathlib import Path
from urllib.parse import urlsplit

import anyio
import yaml
from mcp import Client, Implementation
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

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


# --- rendering (pure: dict in, str out) ---------------------------------------


def _when(iso: str) -> str:
    return datetime.fromisoformat(iso).astimezone(UTC).strftime("%Y-%m-%d %H:%M")


def _table(rows: list[tuple[str, ...]]) -> list[str]:
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    return ["  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip() for r in rows]


def render_summary(d: dict) -> str:
    return f"#{d['id']} {d['title']} [{d['status']}] — {d['waiting_for']}"


def _event_detail(e: dict) -> str:
    """Short extra column: what a gate decision, release or completion carried."""
    d = e["detail"]
    if e["operation"] == "decide_gate":
        return ": ".join(x for x in (d.get("decision"), d.get("reason")) if x)
    if e["operation"] == "release_step":
        return f"{d.get('reason', '')}{' (force)' if d.get('force') else ''}"
    if e["operation"] == "complete_step":
        return ", ".join(a["name"] for a in d.get("artifacts", []))
    return ""


def _event_note(e: dict) -> str:
    """Last history column: the rejection reason first, then what the call carried."""
    detail = _event_detail(e)
    if not e["reason"]:
        return detail
    return f"{e['reason']} ({detail})" if detail else e["reason"]


def render_task_detail(d: dict) -> str:
    lines = [f"#{d['id']} {d['title']} [{d['status']}]"]
    if d["step_id"] is not None:
        lines.append(f"step: {d['current_step']}  (step id {d['step_id']})")
    lines += [f"waiting for: {d['waiting_for']}", "", "artifacts:"]
    arts = [(a["name"], f"{a['path']}@{a['commit'][:12]}", a["branch"], f"{a['step']} r{a['round']}")
            for a in d["artifacts"]]
    lines += ["  " + ln for ln in _table(arts)] if arts else ["  none"]
    lines += ["", "history:"]
    hist = [
        (_when(e["at"]), e["caller"], e["operation"], e["step"] or "-", e["result"], _event_note(e))
        for e in d["history"]
    ]  # fmt: skip
    lines += ["  " + ln for ln in _table(hist)] if hist else ["  none"]
    return "\n".join(lines)


def render_task_list(d: dict) -> str:
    if not d["tasks"]:
        return "no tasks"
    return "\n".join(
        f"#{t['id']} {t['title']} [{t['status']}]  {t['waiting_for']}" for t in d["tasks"]
    )


def render_steps(d: dict) -> str:
    if not d["steps"]:
        return "no open steps"
    return "\n".join(
        f"step {s['step_id']}  task #{s['task_id']} {s['task_title']}  {s['step']} "
        f"(round {s['round']}, skill {s['skill']})"
        for s in d["steps"]
    )


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
            args["reason"] = ns.reason
        return "decide_gate", args, render_summary
    if ns.cmd == "release":
        args = {"step_id": ns.step_id, "reason": ns.reason, "force": True}
        return "release_step", args, render_summary
    return "list_open_steps", {}, render_steps


def _serve(cfg) -> int:
    from taktloop import serve  # the server stack; client commands never load it

    serve.run(cfg)
    return 0


def _install_skill(dest: Path) -> int:
    shutil.copytree(TEMPLATES / "mvp" / "skills" / "taktloop", dest, dirs_exist_ok=True)
    print(dest)
    return 0


def _issuer(ns) -> int:
    from taktloop.issuer.app import build_app  # only the issuer command loads it
    from taktloop.issuer.config import load_issuer_config
    from taktloop.issuer.state import init_state

    try:
        cfg = load_issuer_config(ns.config)
        if ns.sub == "init":
            written = init_state(cfg, ns.credentials, ns.rotate)
            print("\n".join(f"{i}: {ns.credentials / (i + '.json')}" for i in written) or "nothing to do")
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
        print(fetch_token(ns.identity, ns.credentials))
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
    return fetch_token(identity)


def run_client(ns, cfg) -> int:
    url = ns.url or cfg.url
    if not _valid_url(url):
        print(f"taktloop: invalid URL {url}", file=sys.stderr)
        return 2
    tool, args, render = _request(ns)
    try:
        result = anyio.run(_call_tool, url, tool, args, _bearer(ns, cfg))
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

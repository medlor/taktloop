"""The ``taktloop`` command: ``serve`` runs the core, everything else is an MCP client call."""

import argparse
import json
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

from taktloop.config import load_config

TEMPLATES = Path(__file__).resolve().parents[2] / "templates"


class ToolRejected(Exception):
    """The core refused the operation; the message is the reason."""


class CoreUnreachable(Exception):
    """No core is listening at the URL."""


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


async def _call_tool(url: str, name: str, args: dict) -> dict:
    _probe(url)
    info = Implementation(name="taktloop-cli", version=version("taktloop"))
    try:
        async with Client(url, client_info=info) as client:
            result = await client.call_tool(name, args)
    except BaseException as e:
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


def render_task_detail(d: dict) -> str:
    lines = [
        f"#{d['id']} {d['title']} [{d['status']}]",
        f"step: {d['current_step'] or '-'}  (step id {d['step_id'] if d['step_id'] is not None else '-'})",
        f"waiting for: {d['waiting_for']}",
        "",
        "artifacts:",
    ]
    arts = [(a["name"], f"{a['path']}@{a['commit'][:12]}", a["branch"], f"{a['step']} r{a['round']}")
            for a in d["artifacts"]]
    lines += ["  " + ln for ln in _table(arts)] if arts else ["  none"]
    lines += ["", "history:"]
    hist = [
        (_when(e["at"]), e["caller"], e["operation"], e["step"] or "-", e["result"] + (f": {e['reason']}" if e["reason"] else ""), _event_detail(e))
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


# --- commands ------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    def common(parser: argparse.ArgumentParser, **defaults) -> None:
        parser.add_argument("--config", type=Path, **defaults.get("config", {}))
        parser.add_argument("--url", help="core URL (overrides the config; no config is read)",
                            **defaults.get("url", {}))
        parser.add_argument("--json", action="store_true", help="print structured results as JSON",
                            **defaults.get("json", {}))

    p = argparse.ArgumentParser(prog="taktloop")
    p.add_argument("--version", action="version", version=f"%(prog)s {version('taktloop')}")
    common(p, config={"default": Path("factory.yaml")})
    # subparser copies must not overwrite a value given before the subcommand
    hide = {"default": argparse.SUPPRESS}
    shared = argparse.ArgumentParser(add_help=False)
    common(shared, config=hide, url=hide, json=hide)
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("serve", parents=[shared], help="run the core")
    task = sub.add_parser("task", parents=[shared], help="create, list and show tasks").add_subparsers(dest="sub", required=True)
    c = task.add_parser("create", parents=[shared])
    c.add_argument("title")
    c.add_argument("--request", default="", help="request text, or - to read stdin")
    task.add_parser("list", parents=[shared]).add_argument("--all", action="store_true", help="include closed tasks")
    task.add_parser("show", parents=[shared]).add_argument("id", type=int)
    a = sub.add_parser("approve", parents=[shared])
    a.add_argument("task", type=int)
    a.add_argument("--gate")
    r = sub.add_parser("reject", parents=[shared])
    r.add_argument("task", type=int)
    r.add_argument("--reason", required=True)
    r.add_argument("--gate")
    rel = sub.add_parser("release", parents=[shared], help="force-release a claimed step")
    rel.add_argument("step_id", type=int)
    rel.add_argument("--force", action="store_true", required=True)
    rel.add_argument("--reason", required=True)
    sub.add_parser("steps", parents=[shared], help="list open steps")
    s = sub.add_parser("install-skill", parents=[shared], help="copy the bridge skill into a project")
    s.add_argument("--dest", type=Path, default=Path(".claude/skills/taktloop"))
    return p


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
    from datetime import timedelta

    from taktloop.mcp_server import build_server
    from taktloop.service import Service
    from taktloop.store import Store

    cfg.database.parent.mkdir(parents=True, exist_ok=True)
    service = Service(Store(cfg.database), cfg.workflow, timedelta(minutes=cfg.lease_minutes))
    print(f"taktloop core for factory '{cfg.factory}' on {cfg.url}", flush=True)
    build_server(service).run("streamable-http", host=cfg.host, port=cfg.port)
    return 0


def _install_skill(dest: Path) -> int:
    shutil.copytree(TEMPLATES / "mvp" / "skills" / "taktloop", dest, dirs_exist_ok=True)
    print(dest)
    return 0


def main(argv: list[str] | None = None) -> int:
    ns = _parser().parse_args(argv)
    if ns.cmd == "install-skill":
        return _install_skill(ns.dest)
    try:
        cfg = None if (ns.url and ns.cmd != "serve") else load_config(ns.config)
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as e:
        print(f"taktloop: {e}", file=sys.stderr)
        return 2
    if ns.cmd == "serve":
        return _serve(cfg)
    url = ns.url or cfg.url
    if not _valid_url(url):
        print(f"taktloop: invalid URL {url}", file=sys.stderr)
        return 2
    tool, args, render = _request(ns)
    try:
        result = anyio.run(_call_tool, url, tool, args)
    except ToolRejected as e:
        print(f"rejected: {e}", file=sys.stderr)
        return 1
    except CoreUnreachable:
        print(f"taktloop core not reachable at {url} — start it with `uv run taktloop serve`", file=sys.stderr)
        return 2
    except Exception as e:
        while isinstance(e, BaseExceptionGroup):
            e = e.exceptions[0]
        msg = " ".join(f"{type(e).__name__}: {e}".split())
        print(f"taktloop: {msg}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=2) if ns.json else render(result))
    return 0

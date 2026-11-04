"""`taktloop init`, `up` and `connect`: set up a factory directory, run it, connect a product repo to it."""

import json
import os
import secrets
import shutil
import signal
import socket
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from urllib.parse import urlsplit

import yaml

from taktloop.cli_commands import TEMPLATES, _fail, _issuer, _valid_url
from taktloop.config import load_config

FILES = ("factory.yaml", "workflow.yaml", "hooks.yaml", "dev-issuer.yaml")


def _init(ns) -> int:
    d, (owner, _, repo) = ns.dir, ns.repo.partition("/")
    if not owner or not repo or "/" in repo:
        return _fail("taktloop: --repo must be OWNER/REPO")
    if d.exists() and (not d.is_dir() or any(d.iterdir())):
        return _fail(f"taktloop: {d} is not empty")
    if not ns.checkout.is_dir():
        return _fail(f"taktloop: --checkout {ns.checkout} is not a directory")
    if not (TEMPLATES / "mvp").is_dir():
        return _fail(f"taktloop: no templates at {TEMPLATES}; install with `uv tool install --editable <clone>`")
    d.mkdir(parents=True, exist_ok=True)
    checkout = os.path.relpath(ns.checkout.resolve(), d.resolve())
    for name in FILES:
        text = (TEMPLATES / "mvp" / name).read_text().replace("__OWNER__", owner).replace("__REPO__", repo)
        (d / name).write_text(text.replace("{checkout: ..}", f"{{checkout: {json.dumps(checkout)}}}"))
    (d / ".env").touch(mode=0o600)
    (d / ".env").write_text(f"TAKTLOOP_TICKETS_TOKEN={secrets.token_urlsafe(32)}\nTAKTLOOP_GITHUB_TOKEN=\n")
    (d / ".gitignore").write_text(".env\n.taktloop/\n")
    if code := _issuer(SimpleNamespace(config=d / "dev-issuer.yaml", sub="init", credentials=None, rotate=None)):
        return code
    if subprocess.run(["git", "-C", str(d), "rev-parse"], capture_output=True).returncode:
        subprocess.run(["git", "init", "-q", str(d)], check=True)
    print(f"factory in {d}. Next:\n"
          f"  1. put a GitHub token (Actions: read) after TAKTLOOP_GITHUB_TOKEN= in {d / '.env'}\n"
          f"  2. cd {d} && taktloop up\n"
          "  3. in the product repo: taktloop connect http://127.0.0.1:8765/mcp \\\n"
          "       --tickets-url http://127.0.0.1:8767/mcp")
    return 0


def _port(url: str) -> tuple[str, int]:
    parts = urlsplit(url)
    return parts.hostname or "127.0.0.1", parts.port or (443 if parts.scheme == "https" else 80)


def _listening(addr: tuple[str, int]) -> bool:
    try:
        socket.create_connection(addr, timeout=0.5).close()
        return True
    except OSError:
        return False


def _relay(name: str, stream) -> None:
    for line in stream:
        print(f"{name}| {line}", end="", flush=True)


def _stop(procs: list[subprocess.Popen]) -> None:
    for p in procs:
        if p.poll() is None:
            p.send_signal(signal.SIGINT)
    deadline = time.monotonic() + 5
    for p in procs:
        try:
            p.wait(timeout=max(0.0, deadline - time.monotonic()))
        except subprocess.TimeoutExpired:
            p.kill()
            p.wait()


def _up(ns) -> int:
    """Issuer, tickets, core and orchestrator as child processes, until Ctrl-C/SIGTERM/SIGHUP or one exits."""
    try:
        cfg = load_config(ns.config)
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as e:
        return _fail(f"taktloop: {e}")
    config, tickets = str(ns.config.resolve()), cfg.mcp_servers.get("tickets")
    issuer = str(cfg.root.resolve() / "dev-issuer.yaml")
    plan = [("issuer", ["issuer", "--config", issuer], _port(cfg.auth_issuer)),
            *([("tickets", ["tickets", "serve", "--port", str(_port(tickets.url)[1])], _port(tickets.url))]
              if tickets else []),
            ("core", ["--config", config, "serve"], (cfg.host, cfg.port)),
            ("orch", ["--config", config, "orchestrator"], None)]
    procs, relays = [], []
    old = {sig: signal.signal(sig, signal.default_int_handler) for sig in (signal.SIGTERM, signal.SIGHUP)}
    try:
        for name, args, addr in plan:
            if addr and _listening(addr):
                return _fail(f"taktloop: {name} port {addr[1]} is already in use (another factory running?)")
            p = subprocess.Popen([sys.executable, "-m", "taktloop.cli", *args], cwd=cfg.root,
                                 env={**os.environ, "PYTHONUNBUFFERED": "1"}, text=True, stdout=subprocess.PIPE,
                                 stderr=subprocess.STDOUT, start_new_session=True)  # Ctrl-C reaches only `up`
            procs.append(p)
            (relay := threading.Thread(target=_relay, args=(name, p.stdout), daemon=True)).start()
            relays.append(relay)
            deadline = time.monotonic() + 10
            while addr and p.poll() is None and time.monotonic() < deadline and not _listening(addr):
                time.sleep(0.2)
            if p.poll() is not None:
                break
        while not (gone := [(n, p.returncode) for (n, *_), p in zip(plan, procs) if p.poll() is not None]):
            time.sleep(0.2)
        print(f"taktloop: {gone[0][0]} exited ({gone[0][1]}); stopping all", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        return 0
    finally:
        _stop(procs)
        for sig, handler in old.items():
            signal.signal(sig, handler)
        for relay in relays:
            relay.join(timeout=1)


def _connect(ns) -> int:
    """Install the skills into <dest>/.claude/skills and register the core (and stand-in) in .mcp.json."""
    urls = {"taktloop": (ns.core_url, "TAKTLOOP_TOKEN"), "tickets": (ns.tickets_url, "TAKTLOOP_TICKETS_TOKEN")}
    if bad := [u for u, _ in urls.values() if u is not None and not _valid_url(u)]:
        return _fail(f"taktloop: invalid URL {bad[0]}")
    path = ns.dest / ".mcp.json"
    if not (TEMPLATES / "mvp").is_dir():
        return _fail(f"taktloop: no templates at {TEMPLATES}; install with `uv tool install --editable <clone>`")
    try:
        data = json.loads(path.read_text()) if path.exists() else {}
        if not isinstance(servers := data.setdefault("mcpServers", {}), dict):
            raise ValueError("mcpServers is not an object")
    except (ValueError, AttributeError) as e:
        return _fail(f"taktloop: {path} is not a JSON object with mcpServers: {e}")
    for name, (url, env) in urls.items():
        if url:
            servers[name] = {"type": "http", "url": url, "headers": {"Authorization": f"Bearer ${{{env}}}"}}
    shutil.copytree(TEMPLATES / "mvp" / "skills", ns.dest / ".claude" / "skills", dirs_exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")
    print("start a session with:\n"
          "  TAKTLOOP_TOKEN=$(taktloop token agent-a --config <factory dir>/factory.yaml) claude")
    return 0

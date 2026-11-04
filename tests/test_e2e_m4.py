"""The M4 setup split end to end: `taktloop init` into a temp dir, `taktloop up` as a subprocess (issuer,
ticket stand-in, core, orchestrator), a task routed quick at intake opens its ticket and the hook moves the
ticket along, closing the ticket shows the task out of sync, and `up` stops cleanly on Ctrl-C."""

import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable

import pytest

from taktloop.cli import main
from test_e2e_m2 import _free_port

pytestmark = pytest.mark.e2e

GITHUB_URL = "https://api.githubcopilot.com/mcp/x/actions/readonly"
FAKE_CLAUDE = """#!/bin/sh
[ "$1" = auth ] && echo '{"loggedIn": true, "authMethod": "claude.ai"}'
exit 0
"""


def _until(check: Callable[[], object], seconds: float, what: str, log) -> object:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if result := check():
            return result
        time.sleep(0.5)
    pytest.fail(f"no {what} after {seconds} s:\n{log.read_text()}")


def _listening(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
        return True
    except OSError:
        return False


def test_m4_setup_split(tmp_path, monkeypatch, capsys, fake_github):
    d, product = tmp_path / "factory", tmp_path / "product"
    product.mkdir()
    assert main(["init", str(d), "--repo", "medlor/taktloop", "--checkout", str(product)]) == 0
    # free ports, a fast orchestrator and the fake GitHub; then new credentials for the new issuer URL
    issuer, core, tickets = (_free_port() for _ in range(3))
    cfg = d / "factory.yaml"
    text = cfg.read_text()
    for old, new in (("127.0.0.1:8766", f"127.0.0.1:{issuer}"), ("port: 8765", f"port: {core}"),
                     ("127.0.0.1:8767", f"127.0.0.1:{tickets}"), ("tick_seconds: 15", "tick_seconds: 1"),
                     ("every_minutes: 5", "every_minutes: 1"), (GITHUB_URL, fake_github[0])):
        assert old in text, old
        text = text.replace(old, new)
    cfg.write_text(text)
    issuer_cfg = d / "dev-issuer.yaml"
    issuer_cfg.write_text(issuer_cfg.read_text().replace("127.0.0.1:8766", f"127.0.0.1:{issuer}"))
    shutil.rmtree(d / ".taktloop")
    assert main(["issuer", "init", "--config", str(issuer_cfg)]) == 0
    dotenv = d / ".env"
    dotenv.write_text(dotenv.read_text().replace("TAKTLOOP_GITHUB_TOKEN=", "TAKTLOOP_GITHUB_TOKEN=fake-gh"))
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "claude").write_text(FAKE_CLAUDE)
    (bin_dir / "claude").chmod(0o755)
    for name in ("TAKTLOOP_TOKEN", "TAKTLOOP_TICKETS_TOKEN", "TAKTLOOP_GITHUB_TOKEN"):
        monkeypatch.delenv(name, raising=False)  # every token comes from the factory directory
    env = {**os.environ, "PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}"}

    log = tmp_path / "up.log"
    with open(log, "w") as out:
        up = subprocess.Popen([sys.executable, "-m", "taktloop.cli", "--config", str(cfg), "up"], cwd=tmp_path,
                              stdout=out, stderr=subprocess.STDOUT, env=env, start_new_session=True)
    cli = lambda *a: main(["--config", str(cfg), *a])  # noqa: E731

    def output(*args) -> str | None:
        capsys.readouterr()
        return capsys.readouterr().out if cli(*args) == 0 else None

    try:
        _until(lambda: "orch|" in log.read_text(), 40, "orchestrator", log)
        assert cli("task", "create", "Fix a typo in the README", "--route", "quick") == 0

        # open_ticket opens the ticket, the core takes the quick route, the hook follows the steps
        ticket = _until(lambda: (t := output("tickets", "show", "1")) and json.loads(t)["status"] == "in progress"
                        and json.loads(t), 30, "ticket in progress", log)
        assert ticket["title"] == "Fix a typo in the README"
        task = json.loads(output("--json", "task", "show", "1"))
        assert task["current_step"] == "workspace"
        assert [e["detail"]["route"] for e in task["history"] if e["operation"] == "route_at_intake"] == ["quick"]

        # closing the ticket behind the task's back shows it out of sync within every_minutes
        assert cli("tickets", "close", "1") == 0
        _until(lambda: "out of sync" in (output("task", "list") or ""), 90, "out of sync", log)
    finally:
        os.kill(up.pid, signal.SIGINT)
        try:
            up.wait(timeout=20)
        except subprocess.TimeoutExpired:
            os.killpg(up.pid, signal.SIGKILL)
            raise
    text = log.read_text()
    assert up.returncode == 0, text
    for prefix in ("issuer|", "tickets|", "core|", "orch|"):
        assert prefix in text, prefix
    assert not any(_listening(p) for p in (issuer, core, tickets)), text
    assert "fake-gh" not in text and "TAKTLOOP_TICKETS_TOKEN=" not in text

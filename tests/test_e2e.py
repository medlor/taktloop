"""End to end against a real core and a real dev issuer: tokens, restart, Ctrl-C."""

import json
import os
import re
import signal
import socket
import subprocess
import time
from pathlib import Path

import anyio
import pytest
import httpx2
from mcp import Client, Implementation
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from taktloop.cli import main
from taktloop.token_client import fetch_token

ROOT = Path(__file__).resolve().parent.parent
SHA = "a" * 40

pytestmark = pytest.mark.e2e

CONFIG = """\
factory: taktloop  # matches the real_issuer scopes
server: {{host: 127.0.0.1, port: {port}}}
database: {db}
auth: {{issuer: {issuer}, audience: taktloop}}
"""


WORKFLOW = """\
start: design
steps:
  design:
    kind: agent
    role: planner
    skill: brainstorming
    instructions: Write the design doc.
    output:
      artifacts: [design_doc]
    next: design_approval
  design_approval:
    kind: human_gate
    role: approver
    on_approve: plan
    on_reject: design
  plan:
    kind: agent
    role: planner
    skill: writing-plans
    instructions: Write the plan.
    output:
      artifacts: [plan]
    next: done
"""


class Core:
    def __init__(self, tmp_path: Path, issuer: str, token: str):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}/mcp"
        self.dir = tmp_path
        self.cfg = str(tmp_path / "factory.yaml")
        (tmp_path / "workflow.yaml").write_text(WORKFLOW)
        (tmp_path / "factory.yaml").write_text(CONFIG.format(port=port, db=tmp_path / "f.db", issuer=issuer))
        self.token = token
        self.proc: subprocess.Popen | None = None
        self.runs = 0

    def start(self):
        self.runs += 1
        with (open(self.dir / f"out{self.runs}.log", "w") as out,
              open(self.dir / f"err{self.runs}.log", "w") as err):
            self.proc = subprocess.Popen(
                ["uv", "run", "taktloop", "--config", self.cfg, "serve"],
                cwd=ROOT, stdout=out, stderr=err, start_new_session=True,
                env={k: v for k, v in os.environ.items() if k != "TAKTLOOP_TOKEN"},
            )
        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            if self.proc.poll() is None and main(["--url", self.url, "task", "list"]) == 0:
                return
            time.sleep(0.2)
        self.stop()
        pytest.fail("core did not become ready:\n" + (self.dir / f"err{self.runs}.log").read_text())

    def stop(self):
        if self.proc and self.proc.poll() is None:
            os.killpg(self.proc.pid, signal.SIGTERM)  # uv and the core it started
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(self.proc.pid, signal.SIGKILL)
                self.proc.wait()

    def client(self):
        http = create_mcp_http_client(headers={"Authorization": f"Bearer {self.token}"})
        return Client(streamable_http_client(self.url, http_client=http),
                      client_info=Implementation(name="claude-code", version="0"))


@pytest.fixture
def core(tmp_path, real_issuer, monkeypatch):
    issuer, creds = real_issuer
    monkeypatch.setenv("TAKTLOOP_TOKEN", fetch_token("tobias", creds))  # the CLI's identity
    c = Core(tmp_path, issuer, fetch_token("agent-a", creds))
    c.start()
    yield c
    c.stop()


def test_task_survives_restart(core, capsys):
    cli = lambda *a: main(["--url", core.url, *a])  # noqa: E731

    async def call(tool, args):
        async with core.client() as c:
            return await c.call_tool(tool, args)

    def run(tool, **args):
        return anyio.run(call, tool, args)

    assert cli("task", "create", "Add a --version flag") == 0
    step_id = run("list_open_steps").structured_content["steps"][0]["step_id"]
    token = run("claim_step", step_id=step_id).structured_content["claim_token"]

    # a session without the claim token is rejected, for complete and release
    art = [{"name": "design_doc", "path": "docs/design.md", "commit": SHA, "branch": "beta"}]
    r = run("complete_step", step_id=step_id, claim_token="0" * 32, artifacts=art)
    assert r.is_error and "claim token" in r.content[0].text
    r = run("release_step", step_id=step_id, reason="steal")
    assert r.is_error and "claim token" in r.content[0].text

    core.stop()
    core.start()

    r = run("complete_step", step_id=step_id, claim_token=token, artifacts=[
        {"name": "design_doc", "path": "docs/design.md", "commit": SHA, "branch": "beta"}])
    assert not r.is_error, r
    assert cli("approve", "1") == 0

    steps = run("list_open_steps").structured_content["steps"]
    assert [s["step"] for s in steps] == ["plan"]
    plan_id = steps[0]["step_id"]
    tok = run("claim_step", step_id=plan_id).structured_content["claim_token"]
    ctx = run("get_step_context", step_id=plan_id).structured_content
    design = [a for a in ctx["artifacts"] if a["name"] == "design_doc"]
    assert len(design) == 1 and design[0]["commit"] == SHA
    r = run("complete_step", step_id=plan_id, claim_token=tok, artifacts=[
        {"name": "plan", "path": "docs/plan.md", "commit": "b" * 40, "branch": "beta"}])
    assert not r.is_error, r

    capsys.readouterr()
    assert main(["--url", core.url, "--json", "task", "show", "1"]) == 0
    task = json.loads(capsys.readouterr().out)
    assert task["status"] == "done"
    assert len(task["artifacts"]) == 2


def test_request_without_token_gets_401(core):
    r = httpx2.post(core.url, json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                    headers={"Accept": "application/json, text/event-stream"})
    assert r.status_code == 401
    r = httpx2.post(core.url, json={"jsonrpc": "2.0", "id": 1, "method": "ping"},
                    headers={"Accept": "application/json, text/event-stream", "Authorization": "Bearer junk"})
    assert r.status_code == 401
    log = (core.dir / f"err{core.runs}.log").read_text()
    stamp = r"\d{4}-\d\d-\d\d \d\d:\d\d:\d\d,\d+ WARNING taktloop.auth: "
    assert re.search(stamp + r"401 for 127\.0\.0\.1:\d+ /mcp: no bearer token", log), log
    assert re.search(stamp + r"401 for 127\.0\.0\.1:\d+ /mcp: bearer token refused", log), log
    assert re.search(stamp + r"bearer token refused: ", log), log


def test_ctrl_c_stops_core_with_an_open_connection(core):
    """A client holding a request open (as Claude Code does) must not block Ctrl-C."""
    port = int(core.url.split(":")[2].split("/")[0])
    with socket.create_connection(("127.0.0.1", port)) as held:
        held.sendall(b"POST /mcp HTTP/1.1\r\nHost: 127.0.0.1\r\n"
                     b"Content-Type: application/json\r\nContent-Length: 100\r\n\r\n{")
        time.sleep(0.5)
        os.killpg(core.proc.pid, signal.SIGINT)  # what Ctrl-C sends to the terminal's group
        try:
            core.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            pytest.fail("core still running 15 s after Ctrl-C")
    log = (core.dir / f"err{core.runs}.log").read_text()
    assert "Application shutdown complete" in log  # a clean shutdown, not a forced one
    assert "Traceback" not in log

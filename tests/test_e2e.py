"""End to end: a claimed task survives a core restart (M1 acceptance)."""

import json
import socket
import subprocess
import time
from pathlib import Path

import anyio
import pytest
from mcp import Client, Implementation

from taktloop.cli import main

ROOT = Path(__file__).resolve().parent.parent
SHA = "a" * 40

pytestmark = pytest.mark.e2e

CONFIG = """\
factory: e2e
server: {{host: 127.0.0.1, port: {port}}}
database: {db}
workflow:
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
    def __init__(self, tmp_path: Path):
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]
        self.url = f"http://127.0.0.1:{port}/mcp"
        self.dir = tmp_path
        self.cfg = str(tmp_path / "factory.yaml")
        (tmp_path / "factory.yaml").write_text(CONFIG.format(port=port, db=tmp_path / "f.db"))
        self.proc: subprocess.Popen | None = None
        self.runs = 0

    def start(self):
        self.runs += 1
        with (open(self.dir / f"out{self.runs}.log", "w") as out,
              open(self.dir / f"err{self.runs}.log", "w") as err):
            self.proc = subprocess.Popen(
                ["uv", "run", "taktloop", "--config", self.cfg, "serve"],
                cwd=ROOT, stdout=out, stderr=err,
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
            self.proc.terminate()
            try:
                self.proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait()

    def client(self):
        return Client(self.url, client_info=Implementation(name="claude-code", version="0"))


@pytest.fixture
def core(tmp_path):
    c = Core(tmp_path)
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

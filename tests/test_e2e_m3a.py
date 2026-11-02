"""The M3a demo (spec §9) against a real dev issuer and a real core, both subprocesses, with
`separation: strict` and the shipped workflow: plan to merge, code review, and the CI gate the core
checks itself against a fake GitHub MCP server standing in for GitHub's."""

import json
import subprocess

import pytest

from taktloop.cli import main
from taktloop.token_client import fetch_token
from test_e2e_m2 import CORE_HEADER, ROOT, Session, _answers, _free_port, _start, _stop

pytestmark = pytest.mark.e2e

GITHUB_URL = "https://api.githubcopilot.com/mcp/x/actions/readonly"
ISSUER = """\
issuer: http://127.0.0.1:{port}
audience: taktloop
token_lifetime: 12h
state_dir: issuer-state
identities:
  tobias:  {{scopes: [taktloop:taktloop:requester, taktloop:taktloop:approver,
                     taktloop:taktloop:reviewer, taktloop:taktloop:factory_owner]}}
  agent-a: {{scopes: [taktloop:taktloop:planner, taktloop:taktloop:builder,
                     taktloop:taktloop:reviewer, taktloop:taktloop:verifier]}}
  agent-b: {{scopes: [taktloop:taktloop:planner, taktloop:taktloop:builder,
                     taktloop:taktloop:reviewer, taktloop:taktloop:verifier]}}
"""
BRANCH = "taktloop/1-version-flag"
BASE, C1, C2, C3, MERGE = ("0" * 40, "1" * 40, "2" * 40, "3" * 40, "4" * 40)
CI = ".github/workflows/ci.yml"


@pytest.fixture
def factory(tmp_path, monkeypatch, request, fake_github):
    """Issuer and core subprocesses, the core's GitHub server pointed at the fake.
    Yields (core URL, credentials dir, fake GitHub calls)."""
    github_url, calls = fake_github
    issuer = f"http://127.0.0.1:{_free_port()}"
    issuer_cfg = tmp_path / "dev-issuer.yaml"
    issuer_cfg.write_text(ISSUER.format(port=issuer.rsplit(":", 1)[1]))
    creds = tmp_path / "credentials"
    init = subprocess.run(["uv", "run", "taktloop", "issuer", "init", "--config", str(issuer_cfg),
                           "--credentials", str(creds)], cwd=ROOT, capture_output=True, text=True)
    assert init.returncode == 0, init.stderr
    issuer_proc = _start(["issuer", "--config", str(issuer_cfg)], tmp_path / "issuer.log",
                         lambda: _answers(f"{issuer}/jwks.json"))
    request.addfinalizer(lambda: _stop(issuer_proc))

    port = _free_port()
    shipped = (ROOT / "templates/mvp/factory.yaml").read_text()
    assert GITHUB_URL in shipped
    workflow = shipped[shipped.index("mcp_servers:"):].replace(GITHUB_URL, github_url)
    core_cfg = tmp_path / "factory.yaml"
    core_cfg.write_text(CORE_HEADER.format(port=port, db=tmp_path / "f.db", issuer=issuer) + workflow)
    url = f"http://127.0.0.1:{port}/mcp"
    monkeypatch.setenv("TAKTLOOP_GITHUB_TOKEN", "fake")  # the core's own credential
    monkeypatch.setenv("TAKTLOOP_TOKEN", fetch_token("tobias", creds))  # the CLI runs as tobias
    core_proc = _start(["--config", str(core_cfg), "serve"], tmp_path / "core.log",
                       lambda: main(["--url", url, "task", "list"]) == 0)
    yield url, creds, calls
    _stop(core_proc)


class Agent(Session):
    def claim(self, step: str) -> tuple[int, str]:
        step_id = self.open_step(step)
        return step_id, self.call("claim_step", step_id=step_id)["claim_token"]

    def work(self, step: str, artifact: str, commit: str, path: str = ".") -> dict:
        step_id, claim = self.claim(step)
        art = {"name": artifact, "path": path, "commit": commit, "branch": BRANCH}
        return self.call("complete_step", step_id=step_id, claim_token=claim, artifacts=[art])

    def review(self, step: str, outcome: str, findings: str | None = None) -> dict:
        step_id, claim = self.claim(step)
        return self.call("complete_step", step_id=step_id, claim_token=claim, outcome=outcome,
                         findings=findings)


def test_m3a_demo(factory, github_runs, capsys):
    url, creds, calls = factory
    cli = lambda *a: main(["--url", url, *a])  # noqa: E731
    agent_a = Agent(url, fetch_token("agent-a", creds))
    agent_b = Agent(url, fetch_token("agent-b", creds))

    # 1. design, approval, plan and plan review as in M2
    assert cli("task", "create", "Add a --version flag") == 0
    tid = 1
    agent_a.work("design", "design_doc", "a" * 40, "docs/design.md")
    assert cli("approve", str(tid)) == 0
    agent_a.work("plan", "plan", "b" * 40, "docs/plan.md")
    assert agent_b.review("plan_review", "pass")["current_step"] == "workspace"

    # 2. agent-a creates and pushes the branch; the workspace artifact is the base commit
    assert agent_a.work("workspace", "workspace", BASE)["current_step"] == "build"

    # 3. agent-a builds and pushes
    assert agent_a.work("build", "change", C1)["current_step"] == "code_review"

    # 4. agent-a may not review its own change; agent-b returns issues, then passes round 2
    review_id = agent_a.open_step("code_review")
    reason = agent_a.refused("claim_step", step_id=review_id)
    assert reason.endswith(f"strict separation: agent-a completed build round 1 of task {tid}"), reason
    ctx = agent_b.call("get_step_context", step_id=review_id)
    assert (ctx["review_of"], ctx["skill"]) == ("build", "requesting-code-review")
    assert {(a["name"], a["commit"]) for a in ctx["artifacts"]} >= {("workspace", BASE), ("change", C1)}
    assert agent_b.review("code_review", "issues", "1. No test for --version output.")[
        "current_step"] == "build"
    build_id = agent_a.open_step("build")
    ctx = agent_a.call("get_step_context", step_id=build_id)
    assert (ctx["round"], ctx["feedback"]["kind"]) == (2, "review_issues")
    agent_a.work("build", "change", C2)
    assert agent_b.review("code_review", "pass")["current_step"] == "verify"

    # 5. agent-b verifies: a run still going and a run of an older commit are refused
    github_runs["101"] = {"head_sha": C2, "path": CI, "status": "in_progress", "conclusion": None}
    github_runs["100"] = {"head_sha": C1, "path": CI, "status": "completed", "conclusion": "success"}
    github_runs["102"] = {"head_sha": C2, "path": CI, "status": "completed", "conclusion": "failure"}
    verify_id, claim = agent_b.claim("verify")
    ctx = agent_b.call("get_step_context", step_id=verify_id)
    assert ctx["skill"] == "verification-before-completion"
    reason = agent_b.refused("complete_step", step_id=verify_id, claim_token=claim,
                             evidence={"ci_run": "101"})
    assert reason.endswith(": workflow_run.status is in_progress, expected completed"), reason
    reason = agent_b.refused("complete_step", step_id=verify_id, claim_token=claim,
                             evidence={"ci_run": "100"})
    assert reason.endswith(f": workflow_run.head_sha is {C1}, expected {C2}"), reason
    task = agent_b.call("get_task", task_id=tid)  # refusals change nothing
    assert (task["current_step"], task["step_id"]) == ("verify", verify_id)
    assert task["waiting_for"].startswith("verify in progress by agent-b"), task["waiting_for"]

    # 6. a red run on the reviewed commit sends the task back to build with the run as feedback
    red = agent_b.call("complete_step", step_id=verify_id, claim_token=claim,
                       evidence={"ci_run": "102"})
    assert red["current_step"] == "build"
    ctx = agent_a.call("get_step_context", step_id=agent_a.open_step("build"))
    assert (ctx["round"], ctx["feedback"]["kind"], ctx["feedback"]["by"]) == (3, "check_failed", "core")
    assert "ci_run=102" in ctx["feedback"]["text"]
    assert "workflow_run.conclusion is failure, expected success" in ctx["feedback"]["text"]
    agent_a.work("build", "change", C3)
    assert agent_b.review("code_review", "pass")["current_step"] == "verify"
    github_runs["103"] = {"head_sha": C3, "path": CI, "status": "completed", "conclusion": "success"}
    verify_id, claim = agent_b.claim("verify")
    green = agent_b.call("complete_step", step_id=verify_id, claim_token=claim,
                         evidence={"ci_run": "103"})
    assert green["current_step"] == "integration_approval"
    assert {(c["token"], c["method"], c["owner"], c["repo"]) for c in calls} == {
        ("Bearer fake", "get_workflow_run", "medlor", "taktloop")}
    assert [c["resource_id"] for c in calls] == ["101", "100", "102", "103"]

    # 7. tobias approves the integration; agent-a merges and pushes main
    assert cli("approve", str(tid)) == 0
    assert agent_a.work("integrate", "merge", MERGE)["status"] == "done"

    # 8. the history shows the whole lifecycle with check results and the merge commit
    capsys.readouterr()
    assert cli("--json", "task", "show", str(tid)) == 0
    task = json.loads(capsys.readouterr().out)
    assert task["status"] == "done"
    assert [(a["name"], a["commit"], a["path"]) for a in task["artifacts"] if a["name"] == "merge"] == [
        ("merge", MERGE, ".")]
    checks = [e for e in task["history"] if e["operation"] == "check"]
    assert [(e["subject"], e["step"], e["detail"]["result"]) for e in checks] == [
        ("core", "verify", "mismatch"), ("core", "verify", "mismatch"),
        ("core", "verify", "fail"), ("core", "verify", "pass")]
    assert checks[-1]["detail"]["observed"] == {
        "workflow_run.head_sha": C3, "workflow_run.path": CI, "workflow_run.status": "completed",
        "workflow_run.conclusion": "success"}
    assert "fake" not in json.dumps(task)  # the core's credential is in no event
    verdicts = [(e["subject"], e["step"], e["detail"].get("outcome"), e["detail"].get("next"))
                for e in task["history"] if e["operation"] == "complete_step" and e["result"] == "ok"]
    assert verdicts[3:] == [
        ("agent-a", "workspace", "completed", "build"),
        ("agent-a", "build", "completed", "code_review"),
        ("agent-b", "code_review", "issues", "build"),
        ("agent-a", "build", "completed", "code_review"),
        ("agent-b", "code_review", "pass", "verify"),
        ("agent-b", "verify", "failed", "build"),
        ("agent-a", "build", "completed", "code_review"),
        ("agent-b", "code_review", "pass", "verify"),
        ("agent-b", "verify", "completed", "integration_approval"),
        ("agent-a", "integrate", "completed", "done")]

    capsys.readouterr()
    assert cli("task", "show", str(tid)) == 0
    shown = capsys.readouterr().out
    assert "failed → build" in shown
    assert "pass: github.actions_get (ci_run=103): pass" in shown
    assert "fail: github.actions_get (ci_run=102): workflow_run.conclusion is failure" in shown

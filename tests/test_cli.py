from importlib.metadata import version
from pathlib import Path

import pytest

from taktloop import cli, cli_commands

SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"

DETAIL = {
    "id": 1, "title": "Add a --version flag", "status": "active",
    "current_step": "design_approval", "step_id": 2,
    "waiting_for": "waiting for approver decision at design_approval",
    "request": "r",
    "artifacts": [{"name": "design_doc", "path": "docs/d.md", "commit": SHA,
                   "branch": "feat", "step": "design", "round": 1}],
    "history": [{"at": "2026-11-01T09:05:00Z", "subject": "agent-a", "roles": ["planner"], "executor": "claude-code",
                  "operation": "complete_step",
                 "step": "design", "result": "ok", "reason": None, "detail": {}}],
}  # fmt: skip


def test_render_task_detail_lines():
    out = cli_commands.render_task_detail(DETAIL)
    assert "#1 Add a --version flag [active]" in out
    assert "step: design_approval  (step id 2)" in out
    assert "waiting for: waiting for approver decision at design_approval" in out
    assert f"design_doc  docs/d.md@{SHA[:12]}  feat" in out
    assert SHA not in out
    assert "2026-11-01 09:05  agent-a (claude-code)  planner  complete_step  design  ok" in out


def test_render_task_list_and_steps_empty():
    assert cli_commands.render_task_list({"tasks": []}) == "no tasks"
    assert cli_commands.render_steps({"steps": []}) == "no open steps"


def test_render_steps_line():
    d = {"steps": [{"step_id": 3, "task_id": 1, "task_title": "T", "step": "design",
                    "skill": "brainstorming", "round": 1}]}  # fmt: skip
    assert cli_commands.render_steps(d) == "step 3  task #1 T  design (round 1, skill brainstorming)"


def test_core_down_message_and_exit_code(capsys):
    url = "http://127.0.0.1:1/mcp"
    assert cli.main(["--url", url, "task", "list"]) == 2
    err = capsys.readouterr().err
    assert err == f"taktloop core not reachable at {url} — start it with `uv run taktloop serve`\n"


def test_rejection_exit_code(monkeypatch, capsys):
    async def boom(url, name, args, token):
        raise cli_commands.ToolRejected("x")

    monkeypatch.setattr(cli_commands, "_call_tool", boom)
    assert cli.main(["--url", "http://x/mcp", "approve", "1"]) == 1
    assert capsys.readouterr().err == "rejected: x\n"


def test_missing_config_is_one_line(tmp_path, capsys):
    assert cli.main(["--config", str(tmp_path / "nope.yaml"), "steps"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("taktloop: ") and err.count("\n") == 1


def test_reason_strips_sdk_prefix_only_when_present():
    assert cli_commands._reason("Error executing tool decide_gate: task 1 is not at a gate") == "task 1 is not at a gate"
    assert cli_commands._reason("task 1 is not at a gate") == "task 1 is not at a gate"


@pytest.mark.parametrize("url", ["http://127.0.0.1:99999/mcp", "http://127.0.0.1:abc/mcp", "ftp://h/mcp", "http:///mcp"])
def test_invalid_url_is_one_line(url, capsys):
    assert cli.main(["--url", url, "steps"]) == 2
    assert capsys.readouterr().err == f"taktloop: invalid URL {url}\n"


def test_unexpected_error_is_one_line(monkeypatch, capsys):
    async def boom(url, name, args, token):
        raise RuntimeError("boom")

    monkeypatch.setattr(cli_commands, "_call_tool", boom)
    assert cli.main(["--url", "http://x/mcp", "steps"]) == 2
    assert capsys.readouterr().err == "taktloop: RuntimeError: boom\n"


def test_render_task_detail_shows_decisions_and_artifact_rounds():
    d = dict(DETAIL)
    d["artifacts"] = [dict(DETAIL["artifacts"][0], round=2)]
    d["history"] = [
        {"at": "2026-11-01T09:06:00Z", "subject": "me", "roles": ["approver"], "executor": None,
          "operation": "decide_gate",
         "step": "design_approval", "result": "ok", "reason": None,
         "detail": {"decision": "reject", "reason": "needs flags", "artifacts": []}},
        {"at": "2026-11-01T09:07:00Z", "subject": "me", "roles": ["approver"], "executor": None,
          "operation": "release_step",
         "step": "design", "result": "ok", "reason": None,
         "detail": {"reason": "stuck", "force": True}},
        {"at": "2026-11-01T09:08:00Z", "subject": "me", "roles": ["approver"], "executor": None,
          "operation": "complete_step",
         "step": "design", "result": "ok", "reason": None,
         "detail": {"artifacts": [{"name": "design_doc"}]}},
    ]
    out = cli_commands.render_task_detail(d)
    assert "reject: needs flags" in out
    assert "stuck (force)" in out
    assert "design_doc" in out.splitlines()[-1]
    assert "design r2" in out


def test_options_accepted_after_subcommand(monkeypatch, capsys):
    seen = {}

    async def fake(url, name, args, token):
        seen["url"] = url
        return {**DETAIL, "history": []}

    monkeypatch.setattr(cli_commands, "_call_tool", fake)
    assert cli.main(["task", "show", "1", "--json", "--url", "http://x/mcp"]) == 0
    assert seen["url"] == "http://x/mcp" and '"id": 1' in capsys.readouterr().out
    p = cli._parser()
    assert str(p.parse_args(["serve", "--config", "f.yaml"]).config) == "f.yaml"
    ns = p.parse_args(["--url", "http://a/mcp", "steps"])
    assert ns.url == "http://a/mcp" and ns.json is False


@pytest.mark.parametrize("argv", [
    ["--version"],
    ["--json", "--version"],
    ["--config", "/nonexistent/factory.yaml", "--version"],
    ["--url", "http://127.0.0.1:1/mcp", "--version"],
])  # fmt: skip
def test_version_flag(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)
    assert exc.value.code == 0
    out = capsys.readouterr()
    assert out.out == f"taktloop {version('taktloop')}\n"
    assert out.err == ""


def test_version_flag_not_on_subcommand(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["steps", "--version"])
    assert exc.value.code == 2
    assert "unrecognized arguments: --version" in capsys.readouterr().err


def test_render_done_task_has_no_step_line():
    d = dict(DETAIL, status="done", current_step=None, step_id=None, waiting_for="done")
    out = cli_commands.render_task_detail(d)
    assert "step:" not in out
    assert out.splitlines()[1] == "waiting for: done"


def test_rejection_reason_does_not_widen_the_result_column():
    d = dict(DETAIL)
    d["history"] = [
        {"at": "2026-11-01T09:05:00Z", "subject": "me", "roles": ["approver"], "executor": None,
          "operation": "decide_gate",
         "step": "design_approval", "result": "ok", "reason": None,
         "detail": {"decision": "approve"}},
        {"at": "2026-11-01T09:06:00Z", "subject": "me", "roles": ["approver"], "executor": None,
          "operation": "complete_step",
         "step": "design", "result": "rejected", "reason": "claim token does not match",
         "detail": {"artifacts": [{"name": "design_doc"}]}},
    ]
    rows = cli_commands.render_task_detail(d).splitlines()[-2:]
    assert rows[0].endswith("design_approval  ok        approve")
    assert rows[1].endswith("design           rejected  claim token does not match (design_doc)")


def _spy(monkeypatch):
    seen = {}

    async def fake(url, name, args, token):
        seen["token"] = token
        return {"steps": []}

    monkeypatch.setattr(cli_commands, "_call_tool", fake)
    monkeypatch.setattr(cli_commands, "fetch_token", lambda identity, *a: f"fetched-{identity}")
    return seen


def test_token_resolution_order(monkeypatch, tmp_path, capsys):
    seen = _spy(monkeypatch)
    cfg = tmp_path / "factory.yaml"
    cfg.write_text("workflow:\n  start: only\n  steps:\n    only: {kind: agent, role: planner, skill: s, output: {artifacts: [doc]}, next: done}\ncli: {identity: from-config}\nauth: {issuer: http://127.0.0.1:8766, audience: taktloop}\n")
    monkeypatch.delenv("TAKTLOOP_TOKEN")
    assert cli.main(["--config", str(cfg), "steps"]) == 0 and seen["token"] == "fetched-from-config"
    assert cli.main(["--config", str(cfg), "steps", "--as", "me"]) == 0 and seen["token"] == "fetched-me"
    monkeypatch.setenv("TAKTLOOP_TOKEN", "env")
    assert cli.main(["--config", str(cfg), "steps", "--as", "me"]) == 0 and seen["token"] == "env"


def test_no_identity_is_one_line(monkeypatch, capsys):
    _spy(monkeypatch)
    monkeypatch.delenv("TAKTLOOP_TOKEN")
    assert cli.main(["--url", "http://x/mcp", "steps"]) == 2
    assert capsys.readouterr().err == (
        "taktloop: no identity: set TAKTLOOP_TOKEN, pass --as, or set cli.identity in factory.yaml\n"
    )


def test_core_401_is_a_clean_message(capsys):
    """A real HTTP server answering 401, as the core does for a bad or expired token."""
    import threading
    from http.server import BaseHTTPRequestHandler, HTTPServer

    class H(BaseHTTPRequestHandler):
        def do_POST(self):
            self.send_response(401)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, *a):
            pass

    srv = HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        assert cli.main(["--url", f"http://127.0.0.1:{srv.server_port}/mcp", "steps"]) == 2
    finally:
        srv.shutdown()
    assert capsys.readouterr().err == (
        "taktloop: token missing or expired; get a new one with taktloop token <identity>\n"
    )


def test_token_command(real_issuer, capsys):
    url, creds_dir = real_issuer
    assert cli.main(["token", "agent-a", "--credentials", str(creds_dir)]) == 0
    out = capsys.readouterr().out
    assert out.count("\n") == 1 and out.strip().count(".") == 2
    assert cli.main(["token", "nobody", "--credentials", str(creds_dir)]) == 2
    assert "no credentials for nobody" in capsys.readouterr().err


def test_serve_on_an_m1_database_says_delete_it(tmp_path, capsys):
    import sqlite3

    db = tmp_path / "factory.db"
    raw = sqlite3.connect(db)
    raw.execute("PRAGMA user_version = 1")
    raw.close()
    cfg = tmp_path / "factory.yaml"
    cfg.write_text(f"database: {db}\nworkflow:\n  start: only\n  steps:\n    only: {{kind: agent, role: planner, skill: s, output: {{artifacts: [doc]}}, next: done}}\nauth: {{issuer: http://127.0.0.1:8766, audience: taktloop}}\n")
    assert cli.main(["--config", str(cfg), "serve"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("taktloop: ") and f"delete {db} to start fresh" in err


def test_reject_to_sends_target(monkeypatch):
    seen = []

    async def fake(url, name, args, token):
        seen.append((name, args))
        return {"id": 1, "title": "T", "status": "active", "waiting_for": "x"}

    monkeypatch.setattr(cli_commands, "_call_tool", fake)
    base = ["--url", "http://x/mcp", "reject", "1", "--reason", "redo"]
    assert cli.main(base + ["--to", "design"]) == 0
    assert cli.main(base) == 0
    assert seen[0] == ("decide_gate", {"task_id": 1, "decision": "reject", "gate": None,
                                       "reason": "redo", "target": "design"})
    assert seen[1][1]["target"] is None


def _fake_core(monkeypatch, step="plan_review", review_of="plan"):
    seen = []

    async def fake(url, name, args, token):
        seen.append((name, args))
        return {
            "get_task": {"id": 3, "step_id": step and 7, "current_step": step,
                         "status": "active" if step else "done"},
            "get_step_context": {"step_id": 7, "review_of": review_of},
            "claim_step": {"step_id": 7, "claim_token": "tok"},
        }.get(name, {"id": 3, "title": "T", "status": "active", "waiting_for": "x"})

    monkeypatch.setattr(cli_commands, "_call_tool", fake)
    return seen


def test_review_issues_claims_then_completes(monkeypatch):
    seen = _fake_core(monkeypatch)
    assert cli.main(["--url", "http://x/mcp", "review", "3", "--issues", "x"]) == 0
    assert [n for n, _ in seen] == ["get_task", "get_step_context", "claim_step", "complete_step"]
    assert seen[2][1] == {"step_id": 7}
    assert seen[3][1] == {"step_id": 7, "claim_token": "tok", "outcome": "issues", "findings": "x"}


def test_review_pass(monkeypatch):
    seen = _fake_core(monkeypatch)
    assert cli.main(["--url", "http://x/mcp", "review", "3", "--pass"]) == 0
    assert seen[-1][1] == {"step_id": 7, "claim_token": "tok", "outcome": "pass", "findings": None}


def test_review_needs_a_review_step(monkeypatch, capsys):
    seen = _fake_core(monkeypatch, step="plan", review_of=None)
    assert cli.main(["--url", "http://x/mcp", "review", "3", "--pass"]) == 1
    assert capsys.readouterr().err == "rejected: task 3 is at plan, not at a review step\n"
    assert "claim_step" not in [n for n, _ in seen]


def test_review_of_a_done_task(monkeypatch, capsys):
    seen = _fake_core(monkeypatch, step=None, review_of=None)
    assert cli.main(["--url", "http://x/mcp", "review", "3", "--pass"]) == 1
    assert capsys.readouterr().err == "rejected: task 3 has no open step (done)\n"
    assert [n for n, _ in seen] == ["get_task"]


def test_review_needs_pass_or_issues(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--url", "http://x/mcp", "review", "3"])


def test_override_sends_reason_and_step(monkeypatch):
    seen = _fake_core(monkeypatch)
    assert cli.main(["--url", "http://x/mcp", "override", "3", "--reason", "ok"]) == 0
    assert seen[-1] == ("override_review", {"task_id": 3, "reason": "ok",
                                            "review_step": "plan_review"})


def test_review_releases_the_claim_when_the_verdict_is_rejected(monkeypatch, capsys):
    seen = _fake_core(monkeypatch)
    fake = cli_commands._call_tool

    async def refuse_complete(url, name, args, token):
        if name == "complete_step":
            seen.append((name, args))
            raise cli_commands.ToolRejected("findings are required for 'issues'")
        return await fake(url, name, args, token)

    monkeypatch.setattr(cli_commands, "_call_tool", refuse_complete)
    assert cli.main(["--url", "http://x/mcp", "review", "3", "--issues", " "]) == 1
    assert capsys.readouterr().err == "rejected: findings are required for 'issues'\n"
    assert seen[-1] == ("release_step", {"step_id": 7, "claim_token": "tok",
                                         "reason": "taktloop review: verdict rejected"})


def test_review_verdicts_render_outcome_and_next_step():
    d = dict(DETAIL)
    d["history"] = [
        {"at": "2026-11-01T09:05:00Z", "subject": "agent-b", "roles": ["reviewer"],
         "executor": "claude-code", "operation": "complete_step", "step": "plan_review",
         "result": "ok", "reason": None,
         "detail": {"outcome": "issues", "findings": "x", "artifacts": [], "next": "plan_escalation"}},
        {"at": "2026-11-01T09:06:00Z", "subject": "agent-b", "roles": ["reviewer"],
         "executor": "claude-code", "operation": "complete_step", "step": "plan_review",
         "result": "ok", "reason": None,
         "detail": {"outcome": "pass", "findings": None, "artifacts": [], "next": "done"}},
        {"at": "2026-11-01T09:05:00Z", "subject": "core", "roles": [], "executor": None,
         "operation": "escalate", "step": "plan_escalation", "result": "ok", "reason": None,
         "detail": {"review_step": "plan_review", "gate": "plan_escalation", "issue_rounds": 2}},
    ]
    rows = cli_commands.render_task_detail(d).splitlines()[-3:]
    assert rows[0].endswith("plan_review      ok  issues → plan_escalation")
    assert rows[1].endswith("plan_review      ok  pass → done")
    assert " core " in rows[2] and rows[2].endswith(
        "escalate       plan_escalation  ok  plan_review failed 2 times → plan_escalation")


def test_credentials_resolve_next_to_the_config(monkeypatch, tmp_path):
    """--as / cli.identity, `token` and `issuer init` find credentials under the config's
    directory, not the current directory."""
    seen = _spy(monkeypatch)
    dirs = []
    monkeypatch.setattr(cli_commands, "fetch_token", lambda identity, d: dirs.append(d) or "t")
    proj = tmp_path / "proj"
    proj.mkdir()
    cfg = proj / "factory.yaml"
    cfg.write_text("workflow:\n  start: only\n  steps:\n    only: {kind: agent, role: planner, skill: s, output: {artifacts: [doc]}, next: done}\ncli: {identity: me}\nauth: {issuer: http://127.0.0.1:8766, audience: taktloop}\n")
    monkeypatch.delenv("TAKTLOOP_TOKEN")
    monkeypatch.chdir(tmp_path)
    assert cli.main(["--config", str(cfg), "steps"]) == 0 and seen["token"] == "t"
    assert cli.main(["token", "me", "--config", str(cfg)]) == 0
    assert dirs == [proj / ".taktloop/credentials"] * 2
    assert cli.main(["token", "me", "--credentials", "elsewhere"]) == 0
    assert dirs[-1] == Path("elsewhere")


def test_token_with_schemeless_issuer_is_a_message_not_a_traceback(tmp_path, capsys):
    (tmp_path / "x.json").write_text('{"issuer": "issuer.test", "client_id": "x", "client_secret": "s"}')
    assert cli.main(["token", "x", "--credentials", str(tmp_path)]) == 2
    err = capsys.readouterr().err
    assert err.startswith("taktloop: ") and "is not an http(s) URL" in err and "Traceback" not in err

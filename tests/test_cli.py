import pytest

from taktloop import cli

SHA = "a1b2c3d4e5f60718293a4b5c6d7e8f9012345678"

DETAIL = {
    "id": 1, "title": "Add a --version flag", "status": "active",
    "current_step": "design_approval", "step_id": 2,
    "waiting_for": "waiting for approver decision at design_approval",
    "request": "r",
    "artifacts": [{"name": "design_doc", "path": "docs/d.md", "commit": SHA,
                   "branch": "feat", "step": "design", "round": 1}],
    "history": [{"at": "2026-11-01T09:05:00Z", "caller": "claude-code", "operation": "complete_step",
                 "step": "design", "result": "ok", "reason": None, "detail": {}}],
}  # fmt: skip


def test_render_task_detail_lines():
    out = cli.render_task_detail(DETAIL)
    assert "#1 Add a --version flag [active]" in out
    assert "step: design_approval  (step id 2)" in out
    assert "waiting for: waiting for approver decision at design_approval" in out
    assert f"design_doc  docs/d.md@{SHA[:12]}  feat" in out
    assert SHA not in out
    assert "2026-11-01 09:05  claude-code  complete_step  design  ok" in out


def test_render_task_list_and_steps_empty():
    assert cli.render_task_list({"tasks": []}) == "no tasks"
    assert cli.render_steps({"steps": []}) == "no open steps"


def test_render_steps_line():
    d = {"steps": [{"step_id": 3, "task_id": 1, "task_title": "T", "step": "design",
                    "skill": "brainstorming", "round": 1}]}  # fmt: skip
    assert cli.render_steps(d) == "step 3  task #1 T  design (round 1, skill brainstorming)"


def test_core_down_message_and_exit_code(capsys):
    url = "http://127.0.0.1:1/mcp"
    assert cli.main(["--url", url, "task", "list"]) == 2
    err = capsys.readouterr().err
    assert err == f"taktloop core not reachable at {url} — start it with `uv run taktloop serve`\n"


def test_install_skill_copies_bridge_skill(tmp_path):
    assert cli.main(["install-skill", "--dest", str(tmp_path / "s")]) == 0
    assert (tmp_path / "s" / "SKILL.md").exists()


def test_rejection_exit_code(monkeypatch, capsys):
    async def boom(url, name, args):
        raise cli.ToolRejected("x")

    monkeypatch.setattr(cli, "_call_tool", boom)
    assert cli.main(["--url", "http://x/mcp", "approve", "1"]) == 1
    assert capsys.readouterr().err == "rejected: x\n"


def test_missing_config_is_one_line(tmp_path, capsys):
    assert cli.main(["--config", str(tmp_path / "nope.yaml"), "steps"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("taktloop: ") and err.count("\n") == 1


def test_reason_strips_sdk_prefix_only_when_present():
    assert cli._reason("Error executing tool decide_gate: task 1 is not at a gate") == "task 1 is not at a gate"
    assert cli._reason("task 1 is not at a gate") == "task 1 is not at a gate"

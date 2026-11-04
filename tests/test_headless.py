import sys
from pathlib import Path

import pytest

from taktloop import headless
from taktloop.headless import StartFailed, child_env, claude_auth, command, ensure_commits, start

SUB = {"loggedIn": True, "authMethod": "claude.ai"}


def test_child_env_subscription_drops_key():
    env = child_env({"ANTHROPIC_API_KEY": "sk-x", "PATH": "/bin"}, SUB, "tok")
    assert "ANTHROPIC_API_KEY" not in env and env["TAKTLOOP_TOKEN"] == "tok" and env["PATH"] == "/bin"


def test_child_env_falls_back_to_key():
    assert child_env({"ANTHROPIC_API_KEY": "sk-x"}, {"loggedIn": False}, "t")["ANTHROPIC_API_KEY"] == "sk-x"
    assert child_env({"ANTHROPIC_API_KEY": "sk-x"}, None, "t")["ANTHROPIC_API_KEY"] == "sk-x"


def test_child_env_neither_fails():
    with pytest.raises(StartFailed, match="no Claude login and no ANTHROPIC_API_KEY"):
        child_env({}, {"loggedIn": False}, "t")
    with pytest.raises(StartFailed):
        child_env({}, None, "t")


def test_claude_auth_parses_and_fails_soft():
    class R:
        returncode, stdout = 0, '{"loggedIn": true}'

    assert claude_auth(lambda *a, **k: R()) == {"loggedIn": True}
    R.returncode = 1
    assert claude_auth(lambda *a, **k: R()) is None
    R.returncode, R.stdout = 0, "nope"
    assert claude_auth(lambda *a, **k: R()) is None


def test_claude_auth_missing_binary():
    def run(*a, **k):
        raise FileNotFoundError

    assert claude_auth(run) is None


def test_command_is_read_only():
    cmd = command(42, Path("/f"))
    assert cmd[:3] == ["claude", "-p", "work on taktloop step 42"]
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    split = cmd.index("--disallowedTools")
    assert "Edit" in cmd[split:] and "Edit" not in cmd[:split]
    assert cmd[cmd.index("--setting-sources") + 1] == "project,local"
    assert "Read(./.taktloop/**)" in cmd[split:] and "Read(./.env)" in cmd[split:]


def test_command_denies_the_factory_secrets_by_absolute_path():
    cmd = command(42, Path("/home/x/factory"))
    denied = cmd[cmd.index("--disallowedTools"):]
    assert {"Read(//home/x/factory/.taktloop/**)", "Read(//home/x/factory/.env)",
            "Read(./.taktloop/**)", "Read(./.env)"} <= set(denied)


def test_ensure_commits_fetches_once_when_missing(tmp_path):
    calls = []

    class R:
        def __init__(self, rc):
            self.returncode = rc

    def run(cmd, **k):
        calls.append(cmd)
        return R(1 if "cat-file" in cmd else 0)

    ensure_commits(tmp_path, ["a" * 40, "b" * 40], run)
    assert calls.count(["git", "fetch", "origin"]) == 1


def test_start_writes_numbered_logs(tmp_path, monkeypatch):
    monkeypatch.setattr(headless, "claude_auth", lambda: SUB)
    monkeypatch.setattr(headless, "command", lambda *a: [sys.executable, "-c", "print('hi')"])
    for n in (1, 2):
        proc, log = start(7, "tok", tmp_path, {}, [])
        proc.wait()
        assert log == tmp_path / ".taktloop" / "runs" / f"7-{n}.log" and "hi" in log.read_text()


def test_start_without_claude(tmp_path, monkeypatch):
    monkeypatch.setattr(headless, "claude_auth", lambda: SUB)
    monkeypatch.setattr(headless, "command", lambda *a: ["/nonexistent/claude"])
    with pytest.raises(StartFailed, match="claude is not on PATH"):
        start(7, "tok", tmp_path, {}, [])


def test_start_in_checkout_logs_elsewhere(tmp_path, monkeypatch):
    monkeypatch.setattr(headless, "claude_auth", lambda: SUB)
    (checkout := tmp_path / "product").mkdir()
    seen = []
    monkeypatch.setattr(headless, "command", lambda step_id, factory: seen.append(factory) or [
        sys.executable, "-c", "import os; print(os.getcwd())"])
    proc, log = start(7, "tok", checkout, {}, [], tmp_path / "factory")
    proc.wait()
    assert log == tmp_path / "factory" / ".taktloop" / "runs" / "7-1.log"
    assert log.read_text().strip() == str(checkout) and seen == [tmp_path / "factory"]

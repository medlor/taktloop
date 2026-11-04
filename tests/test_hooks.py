import json
import os
import sys

import pytest
import yaml

from taktloop.checks import CheckUnavailable, ToolFailed
from taktloop.hooks import HookFailed, HookRunner, parse_hooks, run_command

TICKET = "{{evidence.open_ticket.ticket}}"
HOOKS = {"hooks": [
    {"name": "ticket-status", "on": "step_opened", "map": {"design": "in design", "plan": "planning"},
     "call": {"mcp_server": "tickets", "tool": "update_status", "args": {"id": TICKET, "status": "{{mapped}}"}}},
    {"name": "ticket-close", "on": "task_closed",
     "call": {"mcp_server": "tickets", "tool": "close_ticket", "args": {"id": TICKET, "resolution": "{{event.status}}"}}},
]}
VALUES = {"evidence.open_ticket.ticket": "4"}


class Feed:
    def __init__(self):
        self.events = []

    def add(self, operation, step=None, detail=None, values=None):
        self.events.append({"id": len(self.events) + 1, "at": "2026-11-01T14:00:00Z", "subject": "core",
                            "operation": operation, "task_id": 1, "step": step, "detail": detail or {},
                            "values": {"task.id": "1", "task.title": "T1", **(values or {})}})

    def __call__(self, after_id):
        page = [e for e in self.events if e["id"] > after_id][:2]  # small pages: paging is exercised
        return {"events": page, "newest_id": page[-1]["id"] if page else after_id}


class Env:
    pass


@pytest.fixture
def runner(tmp_path):
    e = Env()
    e.feed, e.calls, e.log, e.fail, e.commands = Feed(), [], [], {}, []

    def call(server, tool, args):
        e.calls.append((server, tool, args))
        if tool in e.fail:
            raise e.fail[tool]
        return {}

    e.path = tmp_path / "hooks.yaml"
    e.path.write_text(yaml.safe_dump(HOOKS))
    e.cursor = tmp_path / ".taktloop" / "hooks.cursor"
    e.new = lambda: HookRunner(e.path, e.cursor, {"tickets"}, call,
                               lambda cmd, payload: e.commands.append((cmd, payload)), e.log.append)
    e.r = e.new()
    e.r.reload()
    e.tick = lambda down=(): e.r.tick(e.feed, lambda s: s in down)
    e.tick()  # the first tick: every hook starts at the (empty) feed's newest event
    return e


@pytest.mark.parametrize("hook, error", [
    ({"name": "a", "on": "x", "command": "c", "when": 1}, "unknown key 'when'"),
    ({"on": "x", "command": "c"}, "name and on"),
    ({"name": "a", "command": "c"}, "name and on"),
    ({"name": "a", "on": "x"}, "exactly one of call or command"),
    ({"name": "a", "on": "x", "command": "c", "call": {"mcp_server": "tickets", "tool": "t"}},
     "exactly one of call or command"),
    ({"name": "a", "on": "x", "call": {"mcp_server": "jira", "tool": "t"}}, "unknown mcp_server 'jira'"),
])
def test_parse_hooks_refusals(hook, error):
    with pytest.raises(ValueError, match=error):
        parse_hooks({"hooks": [hook]}, {"tickets"})
    with pytest.raises(ValueError, match="duplicate hook name 'a'"):
        parse_hooks({"hooks": [{"name": "a", "on": "x", "command": "c"}] * 2}, {"tickets"})
    assert parse_hooks(None, {"tickets"}) == []


def test_new_hook_starts_at_newest_event(tmp_path, runner):
    runner.feed.add("step_opened", step="design", values=VALUES)
    runner.feed.add("step_opened", step="plan", values=VALUES)
    runner.feed.add("step_opened", step="design", values=VALUES)
    runner.cursor.unlink()  # a very first start with history in the feed
    runner.r = runner.new()
    runner.r.reload()
    runner.tick()
    assert runner.calls == []
    assert json.loads(runner.cursor.read_text()) == {"ticket-status": 3, "ticket-close": 3}
    runner.feed.add("step_opened", step="plan", values=VALUES)
    runner.tick()
    assert runner.calls == [("tickets", "update_status", {"id": "4", "status": "planning"})]


def test_ticket_status_mapped(runner):
    runner.feed.add("step_opened", step="design", values=VALUES)
    runner.feed.add("task_closed", detail={"status": "done"}, values=VALUES)
    runner.tick()
    assert runner.calls == [("tickets", "update_status", {"id": "4", "status": "in design"}),
                            ("tickets", "close_ticket", {"id": "4", "resolution": "done"})]


def test_step_not_in_map_skipped(runner):
    runner.feed.add("step_opened", step="build", values=VALUES)
    runner.tick()
    assert runner.calls == []
    assert runner.log == ["hook ticket-status skipped event 1: step 'build' is not in its map"]
    assert json.loads(runner.cursor.read_text())["ticket-status"] == 1


def test_hook_skips_unresolved_placeholder(runner):
    runner.feed.add("step_opened", step="design")  # no ticket yet
    runner.feed.add("step_opened", step="plan", values=VALUES)
    runner.tick()
    assert runner.calls == [("tickets", "update_status", {"id": "4", "status": "planning"})]
    assert runner.log == ["hook ticket-status skipped event 1: cannot resolve {{evidence.open_ticket.ticket}}"]
    assert json.loads(runner.cursor.read_text())["ticket-status"] == 2


def test_failing_hook_given_up_after_three(runner, tmp_path):
    runner.fail["update_status"] = ToolFailed("ticket 4 is closed")
    runner.feed.add("step_opened", step="design", values=VALUES)
    for _ in range(3):
        runner.tick()
    gave_up = "hook ticket-status gave up on event 1: ticket 4 is closed"
    assert len(runner.calls) == 3 and runner.log == [gave_up]
    assert (tmp_path / ".taktloop" / "hooks.log").read_text() == gave_up + "\n"
    runner.tick()
    assert len(runner.calls) == 3


def test_hook_waits_while_server_unavailable(runner):
    runner.feed.add("step_opened", step="design", values=VALUES)
    runner.feed.add("step_opened", step="plan", values=VALUES)
    runner.feed.add("task_closed", detail={"status": "done"}, values=VALUES)
    for _ in range(5):
        runner.tick(down={"tickets"})
    assert runner.calls == []
    down = CheckUnavailable("tickets is unreachable: ConnectError")
    runner.fail.update(update_status=down, close_ticket=down)
    for _ in range(5):  # unreachable while calling: waits too, no attempt counted
        runner.tick()
    assert len(runner.calls) == 10 and runner.log == []
    runner.fail.clear()
    runner.calls.clear()
    runner.tick()
    assert runner.calls == [("tickets", "update_status", {"id": "4", "status": "in design"}),
                            ("tickets", "update_status", {"id": "4", "status": "planning"}),
                            ("tickets", "close_ticket", {"id": "4", "resolution": "done"})]


def test_one_stuck_hook_does_not_block_another(runner):
    runner.fail["update_status"] = ToolFailed("boom")
    runner.feed.add("step_opened", step="design", values=VALUES)
    runner.feed.add("task_closed", detail={"status": "cancelled"}, values=VALUES)
    runner.tick()
    assert ("tickets", "close_ticket", {"id": "4", "resolution": "cancelled"}) in runner.calls
    assert json.loads(runner.cursor.read_text()) == {"ticket-status": 0, "ticket-close": 2}


def test_reload_on_change_and_invalid_file_kept(runner):
    runner.path.write_text("hooks: [{name: notify, on: report_stall, command: ./notify.sh}]")
    os.utime(runner.path, ns=(1, 1))
    runner.r.reload()
    assert [h.name for h in runner.r.hooks] == ["notify"]
    runner.path.write_text("hooks: [{name: notify, on: report_stall}]")
    os.utime(runner.path, ns=(2, 2))
    runner.r.reload()
    assert [h.name for h in runner.r.hooks] == ["notify"]
    assert runner.log == ["hooks.yaml refused: hook notify: needs exactly one of call or command; "
                          "keeping the previous hooks"]
    runner.tick()  # the new hook starts at the newest event
    runner.feed.add("report_stall", step="build", detail={"reason": "r"})
    runner.tick()
    assert [(c, p["operation"]) for c, p in runner.commands] == [("./notify.sh", "report_stall")]
    runner.path.unlink()
    runner.r.reload()
    assert runner.r.hooks == []


def test_command_hook_gets_json_without_tokens(tmp_path):
    script = tmp_path / "hook.py"
    script.write_text("import json, os, sys\n"
                      "open('out.json', 'w').write(json.dumps({'stdin': json.load(sys.stdin), 'env': dict(os.environ)}))\n"
                      "sys.exit(int(os.environ.get('EXIT', '0')))\n")
    environ = {"PATH": os.environ["PATH"], "TAKTLOOP_TOKEN": "t", "TAKTLOOP_GITHUB_TOKEN": "g",
               "GITHUB_PAT": "p", "HOME": "/home/x"}
    run_command(f"{sys.executable} hook.py", {"operation": "report_stall"}, tmp_path, environ, ["GITHUB_PAT"])
    out = json.loads((tmp_path / "out.json").read_text())
    assert out["stdin"] == {"operation": "report_stall"}
    assert not {"TAKTLOOP_TOKEN", "TAKTLOOP_GITHUB_TOKEN", "GITHUB_PAT"} & set(out["env"])
    assert out["env"]["HOME"] == "/home/x"
    with pytest.raises(HookFailed, match="exit 3"):
        run_command(f"{sys.executable} hook.py", {}, tmp_path, {**environ, "EXIT": "3"}, [])
    with pytest.raises(HookFailed, match="timed out after 0.2 s"):
        run_command(f"{sys.executable} -c 'import time; time.sleep(5)'", {}, tmp_path, environ, [], timeout=0.2)

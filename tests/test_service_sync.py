import pytest
import yaml

from taktloop.checks import CheckDecl, CheckUnavailable, ToolFailed
from taktloop.config import SyncConfig
from taktloop.service import Caller, Rejected, Service
from taktloop.workflow import parse_workflow

WF = parse_workflow(yaml.safe_load("""
start: open_ticket
steps:
  open_ticket: {kind: code, role: orchestrator, next: work, call: {mcp_server: tickets, tool: t, evidence: {ticket: id}}}
  work: {kind: agent, role: builder, skill: s, output: {artifacts: [x]}, next: done}
"""))
SYNC = {"ticket": SyncConfig(CheckDecl("tickets", "get_ticket", {"id": "{{evidence.open_ticket.ticket}}"},
                                       {}, {"closed": "False"}))}
ORCH = Caller("orchestrator", frozenset({"orchestrator"}))
REQ = Caller("tobias", frozenset({"requester"}))


class Env:
    ticket = {"id": 1, "closed": False}
    result = None  # an exception to raise instead of the ticket

    def checker(self, server, tool, args):
        self.calls.append(args)
        if self.result:
            raise self.result
        return self.ticket


@pytest.fixture
def env(store, clock):
    e = Env()
    e.calls, e.ticket = [], dict(Env.ticket)
    e.svc = Service(store, WF, clock=clock, checker=e.checker, sync=SYNC)
    e.task = e.svc.create_task(REQ, "T").id
    return e


def opened(env):
    sid = env.svc.get_task(ORCH, env.task).step_id
    c = env.svc.claim_step(ORCH, sid)
    env.svc.complete_step(ORCH, sid, c.claim_token, "completed", [], evidence={"ticket": "1"})


def ops(env):
    return [e.operation for e in env.svc.get_task(ORCH, env.task).history]


def test_in_sync_no_event(env):
    opened(env)
    s = env.svc.check_sync(ORCH, env.task)
    assert "out of sync" not in s.waiting_for and env.calls == [{"id": "1"}]
    assert "in_sync" not in ops(env) and "out_of_sync" not in ops(env)


def test_closed_ticket_flags_once(env):
    opened(env)
    env.ticket["closed"] = True
    env.svc.check_sync(ORCH, env.task)
    s = env.svc.check_sync(ORCH, env.task)
    assert s.waiting_for.endswith("— out of sync: ticket: closed is True, expected False")
    assert ops(env).count("out_of_sync") == 1
    assert env.svc.list_tasks(ORCH)[0].waiting_for == s.waiting_for


def test_deleted_ticket_flags(env):
    opened(env)
    env.result = ToolFailed("unknown ticket 1")
    assert "ticket: unknown ticket 1" in env.svc.check_sync(ORCH, env.task).waiting_for


def test_unreachable_changes_nothing(env):
    opened(env)
    env.result = CheckUnavailable("tickets is unreachable")
    assert "out of sync" not in env.svc.check_sync(ORCH, env.task).waiting_for
    env.ticket["closed"], env.result = True, None
    env.svc.check_sync(ORCH, env.task)
    env.result = CheckUnavailable("tickets is unreachable")
    assert "out of sync" in env.svc.check_sync(ORCH, env.task).waiting_for
    assert ops(env).count("out_of_sync") == 1 and "in_sync" not in ops(env)


def test_back_in_sync(env):
    opened(env)
    env.ticket["closed"] = True
    env.svc.check_sync(ORCH, env.task)
    env.ticket["closed"] = False
    s = env.svc.check_sync(ORCH, env.task)
    env.svc.check_sync(ORCH, env.task)
    assert "out of sync" not in s.waiting_for and ops(env).count("in_sync") == 1


def test_sync_skips_task_without_ticket(env):
    s = env.svc.check_sync(ORCH, env.task)
    assert env.calls == [] and "out of sync" not in s.waiting_for
    assert "out_of_sync" not in ops(env)


def test_check_sync_needs_orchestrator(env):
    with pytest.raises(Rejected, match="requires role orchestrator"):
        env.svc.check_sync(REQ, env.task)

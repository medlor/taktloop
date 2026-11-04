import pytest

from conftest import MVP

from taktloop.availability import Availability, needed_tools
from taktloop.checks import CheckUnavailable
from taktloop.config import SyncConfig
from taktloop.models import FactoryStatus
from taktloop.service import Caller, Rejected, Service
from taktloop.workflow import CheckDecl, parse_workflow

OWNER = Caller("tobias", frozenset({"factory_owner", "requester"}))
CHECK = {"mcp_server": "github", "tool": "actions_get", "expect": {"a": "b"}}
CODE = {"kind": "code", "role": "steward", "next": "done",
        "call": {"mcp_server": "tickets", "tool": "get_ticket", "evidence": {"k": "id"}}}
WF = {"start": "build", "steps": {
    "build": {"kind": "agent", "role": "builder", "skill": "s", "output": {"artifacts": ["x"]},
              "check": CHECK, "evidence": ["x"], "on_fail": "build", "next": "open"},
    "open": CODE}}


class Lister:
    def __init__(self, tools=None):
        self.calls, self.tools = 0, tools or {}

    def __call__(self, server):
        self.calls += 1
        tools = self.tools.get(server)
        if tools is None:
            raise CheckUnavailable(f"{server} is unreachable: ConnectError")
        return tools


def test_needed_tools_core_vs_orchestrator():
    wf = parse_workflow(WF)
    sync = {"s": SyncConfig(CheckDecl("jira", "search", {}, {}, {"a": "b"}))}
    assert needed_tools(wf, ("check",), sync) == {"github": {"actions_get"}, "jira": {"search"}}
    assert needed_tools(wf, ("call",), extra={"tickets": {"create_ticket"}}) == {
        "tickets": {"get_ticket", "create_ticket"}}


def test_problems_cached_then_rechecked():
    lister, now = Lister({"github": {"actions_get"}}), [0.0]
    avail = Availability(lister, lambda: {"github": {"actions_get"}}, clock=lambda: now[0])
    assert avail.problems() == {} and avail.problems() == {} and lister.calls == 1
    now[0] = 61.0
    avail.problems()
    assert lister.calls == 2


def test_missing_tool_named():
    avail = Availability(Lister({"tickets": {"x"}}), lambda: {})
    assert avail.check({"tickets": {"get_ticket", "x"}}) == "tickets missing tool get_ticket"
    assert avail.check({"tickets": {"x"}}) is None
    assert avail.check({"gone": {"x"}}) == "gone unreachable: ConnectError"


def make(store, clock, lister, now, wf=MVP, **kw):
    avail = Availability(lister, lambda: {"tickets": {"get_ticket"}}, clock=lambda: now[0])
    return avail, Service(store, parse_workflow(wf), clock=clock, availability=avail,
                          servers=["tickets", "github"], **kw)


def test_create_task_refused_while_unavailable(store, clock):
    lister, now = Lister(), [0.0]
    _, svc = make(store, clock, lister, now)
    with pytest.raises(Rejected, match="factory not available: tickets unreachable: ConnectError"):
        svc.create_task(OWNER, "t")
    lister.tools = {"tickets": {"get_ticket"}}
    with pytest.raises(Rejected, match="factory not available"):  # still cached
        svc.create_task(OWNER, "t")
    now[0] = 61.0
    assert svc.create_task(OWNER, "t").title == "t"


def test_approve_refused_for_missing_tool(store, clock):
    lister, now = Lister({"tickets": set()}), [0.0]
    avail, svc = make(store, clock, lister, now, WF)
    svc._tools_check = lambda wf: avail.check(needed_tools(wf, ("check", "call")))
    svc.put_step(OWNER, "open", CODE)
    with pytest.raises(Rejected, match="tickets missing tool get_ticket"):
        svc.approve_workflow(OWNER, 1)


def test_get_factory_reports_servers(store, clock):
    lister, now = Lister({"tickets": {"get_ticket"}}), [0.0]
    _, svc = make(store, clock, lister, now)
    assert svc.get_factory(OWNER, "f") == FactoryStatus(
        factory="f", workflow_version=1, draft_problems=None, servers={"github": "ok", "tickets": "ok"})
    lister.tools = {}
    now[0] = 61.0
    assert svc.get_factory(OWNER, "f").servers == {"github": "ok", "tickets": "unreachable: ConnectError"}


def test_get_factory_probes_outside_transaction(store, clock):
    def lister(server):
        assert not store._conn.in_transaction
        return {"get_ticket"}
    avail = Availability(lambda s: lister(s) and {"get_ticket"}, lambda: {"tickets": {"get_ticket"}})
    svc = Service(store, parse_workflow(MVP), clock=clock, availability=avail, servers=["tickets"])
    assert svc.get_factory(OWNER, "f").servers == {"tickets": "ok"}


def test_unconfigured_server_is_a_problem():
    avail = Availability(lambda s: (_ for _ in ()).throw(CheckUnavailable(f"{s} is not configured")), lambda: {})
    assert avail.check({"x": {"t"}}) == "x not configured"

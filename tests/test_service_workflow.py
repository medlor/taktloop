import pytest
import yaml

from conftest import MVP, SHA

from taktloop import workflow_file
from taktloop.models import ArtifactIn
from taktloop.service import Caller, Rejected, Service
from taktloop.workflow import parse_workflow

ASSIST = Caller("assistant", frozenset({"factory_assistant"}))
OWNER = Caller("tobias", frozenset({"factory_owner", "requester", "approver", "planner"}))
PLANNER = Caller("agent-a", frozenset({"planner"}))
LINT = {"kind": "agent", "role": "builder", "skill": "s", "output": {"artifacts": ["x"]},
        "next": "done", "description": "Run the linter"}
CODE = {"kind": "code", "role": "steward", "next": "done",
        "call": {"mcp_server": "jira", "tool": "t", "evidence": {"k": "id"}}}


@pytest.fixture
def svc(store, clock, tmp_path):
    path = tmp_path / "workflow.yaml"
    workflow_file.write(path, MVP, 1)
    return Service(store, parse_workflow(MVP), clock=clock, servers=["github"], workflow_path=path)


def wire(svc):
    """Draft: lint added after plan, no problems."""
    svc.put_step(ASSIST, "lint", LINT)
    return svc.put_step(ASSIST, "plan", {**MVP["steps"]["plan"], "next": "lint"})


def events(store, op):
    with store.transaction() as tx:
        return [e for e in tx.events_after(0, 1000) if e.operation == op]


def reason(call, *args):
    with pytest.raises(Rejected) as e:
        call(*args)
    return e.value.reason


def test_put_step_gap_is_a_problem_then_fixed(svc):
    v = svc.put_step(ASSIST, "lint", {**LINT, "next": "nowhere"})
    assert v.draft and v.base_version == 1 and any("nowhere" in p for p in v.problems)
    v = svc.put_step(ASSIST, "lint", {**LINT, "next": "plan"})
    assert not any("nowhere" in p for p in v.problems)


def test_illegal_step_refused_draft_unchanged(svc, store):
    with pytest.raises(Rejected):
        svc.put_step(ASSIST, "x", {"kind": "teleport", "role": "r"})
    with pytest.raises(Rejected, match="mcp_server"):
        svc.put_step(ASSIST, "x", CODE)
    with pytest.raises(Rejected, match="reserved"):
        svc.put_step(ASSIST, "done", LINT)
    with pytest.raises(Rejected, match="no draft"):
        svc.get_workflow(ASSIST, draft=True)
    with store.transaction() as tx:  # recorded in the history, all rejected, no draft made
        rows = tx._c.execute("SELECT result FROM events WHERE operation='put_step'").fetchall()
        assert [r[0] for r in rows] == ["rejected"] * 3 and tx.get_draft() is None


@pytest.mark.parametrize("bad", [{"next": ["a"]}, {"next": 3}, {"output": {"artifacts": ["x"]}, "next": {"a": 1}},
                                 {"output": "x"}, {"output": {"artifacts": 3}}, {"output": {"artifacts": [["a"]]}}])
def test_non_string_target_refused_not_crashing(svc, bad):
    with pytest.raises(Rejected):
        svc.put_step(ASSIST, "lint", {**LINT, **bad})
    with pytest.raises(Rejected):
        svc.put_step(ASSIST, "g", {"kind": "human_gate", "role": "approver", "on_approve": ["a"], "on_reject": "plan"})
    with pytest.raises(Rejected):
        svc.put_step(ASSIST, "t", {"kind": "agent", "role": "triage", "skill": "s", "routes": {"quick": ["a"]}})


def test_first_edit_copies_live_and_remove_and_start(svc):
    v = svc.remove_step(ASSIST, "plan")
    assert v.base_version == 1 and "plan" not in v.data["steps"]
    assert any("plan" in p for p in v.problems)  # design_approval points to it
    v = svc.set_start(ASSIST, "design_approval")
    assert v.data["start"] == "design_approval"
    assert "plan" in svc.get_workflow(ASSIST).data["steps"]  # live untouched
    svc.discard_draft(ASSIST)
    assert reason(svc.get_workflow, ASSIST, 1, True) == "no draft"
    svc.discard_draft(ASSIST)  # nothing to drop: no-op
    assert svc.remove_step(ASSIST, "ghost").base_version == 1  # no-op, but it starts a draft
    assert reason(svc.get_workflow, ASSIST, 0) == "unknown version 0"


def test_put_step_keeps_position(svc):
    v = svc.put_step(ASSIST, "design_approval", {**MVP["steps"]["design_approval"], "description": "d"})
    assert list(v.data["steps"]) == ["design", "design_approval", "plan"]
    v = svc.put_step(ASSIST, "lint", LINT)
    assert list(v.data["steps"]) == ["design", "design_approval", "plan", "lint"]
    v = svc.put_step(ASSIST, "design", MVP["steps"]["design"])
    assert list(v.data["steps"]) == ["design", "design_approval", "plan", "lint"]


def test_roles(svc):
    assert "requires role factory_assistant or factory_owner" in reason(svc.put_step, PLANNER, "lint", LINT)
    svc.put_step(OWNER, "lint", LINT)
    assert "requires role factory_owner" in reason(svc.approve_workflow, ASSIST, 1)
    assert svc.get_workflow(PLANNER).version == 1


def test_approve_refusals(svc, store):
    assert reason(svc.approve_workflow, OWNER, 1) == "no draft"
    svc.put_step(ASSIST, "lint", LINT)  # unreachable
    assert reason(svc.approve_workflow, OWNER, 1).startswith("draft has problems: ")
    wire(svc)
    assert reason(svc.approve_workflow, OWNER, 7) == "draft is based on version 1, live is 1"
    svc._tools_check = lambda wf: "tool x missing on github"
    assert reason(svc.approve_workflow, OWNER, 1) == "tool x missing on github"
    assert svc.live_version == 1 and svc.get_workflow(OWNER, draft=True).draft


def test_stale_base_refused(svc):
    wire(svc)
    svc.approve_workflow(OWNER, 1)
    with svc._store.transaction() as tx:  # a draft left over from version 1
        tx.put_draft(1, svc.live.data, "assistant", svc._clock())
    assert reason(svc.approve_workflow, OWNER, 1) == "draft is based on version 1, live is 2"


def test_tools_check_not_called_for_a_stale_base(svc):
    wire(svc)
    svc._tools_check = lambda wf: pytest.fail("network call for a refused approval")
    assert reason(svc.approve_workflow, OWNER, 7).startswith("draft is based on version 1")


def test_tools_check_runs_outside_transaction_and_sees_draft(svc):
    seen = []
    def check(wf):
        assert not svc._store._conn.in_transaction
        seen.append(set(wf.steps))
    svc._tools_check = check
    wire(svc)
    svc.approve_workflow(OWNER, 1)
    assert seen == [{"design", "design_approval", "plan", "lint"}]


def test_draft_changed_during_tools_check_refused(svc):
    wire(svc)
    svc._tools_check = lambda wf: svc.put_step(ASSIST, "lint", {**LINT, "description": "x"}) and None
    assert "changed" in reason(svc.approve_workflow, OWNER, 1)
    assert svc.live_version == 1


def test_approve_writes_file_and_bumps(svc, store):
    wire(svc)
    v = svc.approve_workflow(OWNER, 1)
    assert v.version == 2 and not v.draft and svc.live_version == 2
    f = workflow_file.read(svc._workflow_path)
    assert f.version == 2 and f.step("lint").description == "Run the linter"
    assert yaml.safe_load(svc._workflow_path.read_text())["version"] == 2
    (e,) = events(store, "workflow_changed")
    assert e.detail == {"version": 2, "source": "mcp", "by": "tobias"} and e.subject == "core"
    assert reason(svc.get_workflow, OWNER, 1, True) == "no draft"
    with store.transaction() as tx:
        row = tx.get_workflow_version(2)
        assert (row.source, row.approved_by) == ("mcp", "tobias")


def test_file_write_error_rolls_back(svc, tmp_path):
    wire(svc)
    svc._workflow_path = tmp_path / "missing" / "workflow.yaml"
    with pytest.raises(OSError):
        svc.approve_workflow(OWNER, 1)
    assert svc.live_version == 1 and svc.get_workflow(OWNER, draft=True).draft
    with svc._store.transaction() as tx:
        assert tx.get_workflow_version(2) is None


def test_running_task_stays_on_its_version(svc):
    t1 = svc.create_task(OWNER, "old")
    c = svc.claim_step(OWNER, t1.step_id)
    svc.complete_step(OWNER, t1.step_id, c.claim_token, "completed",
                      [ArtifactIn(name="design_doc", path="d.md", commit=SHA, branch="b")])
    svc.decide_gate(OWNER, t1.id, "design_approval", "approve")
    wire(svc)
    svc.approve_workflow(OWNER, 1)
    svc.remove_step(OWNER, "plan")  # v3 renames plan, the step t1 is heading to
    svc.put_step(OWNER, "planning", {**MVP["steps"]["plan"], "next": "lint"})
    svc.put_step(OWNER, "design_approval", {**MVP["steps"]["design_approval"], "on_approve": "planning"})
    svc.approve_workflow(OWNER, 2)

    def finish(t):
        c = svc.claim_step(OWNER, t.step_id)
        return svc.complete_step(OWNER, t.step_id, c.claim_token, "completed",
                                 [ArtifactIn(name="plan", path="p.md", commit=SHA, branch="b")])
    assert svc.get_task(OWNER, t1.id).current_step == "plan"  # v1
    assert finish(svc.get_task(OWNER, t1.id)).status == "done"
    t2 = svc.create_task(OWNER, "new")
    c = svc.claim_step(OWNER, t2.step_id)
    svc.complete_step(OWNER, t2.step_id, c.claim_token, "completed",
                      [ArtifactIn(name="design_doc", path="d.md", commit=SHA, branch="b")])
    svc.decide_gate(OWNER, t2.id, "design_approval", "approve")
    assert svc.get_task(OWNER, t2.id).current_step == "planning"  # v3
    assert finish(svc.get_task(OWNER, t2.id)).current_step == "lint"

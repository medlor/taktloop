from pathlib import Path

import pytest

from taktloop import workflow_file
from taktloop.config import load_config
from taktloop.workflow import WorkflowError

FACTORY = """
factory: demo
auth: {issuer: http://127.0.0.1:8766, audience: taktloop}
"""

ONLY = """\
start: only
steps:
  only:
    kind: agent
    role: planner
    skill: brainstorming
    output: {artifacts: [doc]}
    next: done
"""


def put(tmp_path: Path, factory: str = FACTORY, workflow: str = ONLY) -> Path:
    (tmp_path / "workflow.yaml").write_text(workflow)
    (tmp_path / "factory.yaml").write_text(factory)
    return tmp_path / "factory.yaml"


def test_defaults_and_relative_db(tmp_path: Path):
    cfg = load_config(put(tmp_path))
    assert cfg.factory == "demo"
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 8765
    assert cfg.lease_minutes == 30
    assert cfg.database == tmp_path / ".taktloop/factory.db"
    assert cfg.url == "http://127.0.0.1:8765/mcp"
    assert cfg.workflow.start == "only"
    assert cfg.workflow.version == 1
    assert cfg.workflow_path == tmp_path / "workflow.yaml"
    assert cfg.product_checkout == tmp_path
    assert cfg.hooks_path == tmp_path / "hooks.yaml"
    assert cfg.sync == {}
    assert cfg.auth_issuer == "http://127.0.0.1:8766"
    assert cfg.auth_audience == "taktloop"


@pytest.mark.parametrize("auth", ["", "auth: {issuer: http://127.0.0.1:8766}\n", "auth: {audience: taktloop}\n"])
def test_auth_block_is_required(tmp_path: Path, auth: str):
    path = put(tmp_path, "factory: demo\n" + auth)
    with pytest.raises(WorkflowError, match="factory.yaml: 'auth' block with issuer and audience is required"):
        load_config(path)


@pytest.mark.parametrize("line, want", [("", "off"), ("separation: off\n", "off"),
                                        ("separation: strict\n", "strict")])
def test_separation(tmp_path: Path, line: str, want: str):
    assert load_config(put(tmp_path, line + FACTORY)).separation == want


def test_separation_rejects_other_values(tmp_path: Path):
    with pytest.raises(WorkflowError, match="separation"):
        load_config(put(tmp_path, "separation: loose\n" + FACTORY))


CHECKED = ONLY.replace("    next: done", """    next: done
    check: {mcp_server: github, tool: t, expect: {a: b}}
    evidence: [x]
    on_fail: only""")


def test_mcp_servers_load(tmp_path: Path):
    path = put(tmp_path, FACTORY + "mcp_servers:\n  github: {url: https://h/mcp, token_env: GH_TOK}\n", CHECKED)
    cfg = load_config(path)
    assert cfg.mcp_servers["github"].url == "https://h/mcp"
    assert cfg.mcp_servers["github"].token_env == "GH_TOK"
    assert cfg.env_file == tmp_path / ".env"
    path = put(tmp_path, FACTORY + "mcp_servers:\n  github: {url: https://h/mcp}\n")
    with pytest.raises(WorkflowError, match="token_env"):
        load_config(path)


def test_check_with_unknown_server_is_refused(tmp_path: Path):
    with pytest.raises(WorkflowError, match="step 'only': check uses unknown mcp_server 'github'"):
        load_config(put(tmp_path, workflow=CHECKED))


def test_orchestrator_block(tmp_path: Path):
    path = put(tmp_path)
    cfg = load_config(path)
    assert cfg.orchestrator is None
    assert cfg.root == path.parent
    put(tmp_path, FACTORY + "orchestrator: {identity: orchestrator}\n")
    orch = load_config(path).orchestrator
    assert (orch.identity, orch.tick_seconds, orch.code_retry_seconds) == ("orchestrator", 15, 60)
    assert (orch.stall_after_minutes, orch.max_lease_expiries) == (60, 2)
    put(tmp_path, FACTORY + "orchestrator: {tick_seconds: 5}\n")
    with pytest.raises(WorkflowError, match="identity"):
        load_config(path)


def test_code_step_unknown_server(tmp_path: Path):
    code = ONLY.replace("""    kind: agent
    role: planner
    skill: brainstorming
    output: {artifacts: [doc]}""", """    kind: code
    role: verifier
    call: {mcp_server: gitlab, tool: t, evidence: {x: id}}""")
    with pytest.raises(WorkflowError, match="gitlab"):
        load_config(put(tmp_path, workflow=code))


def test_workflow_read_from_workflow_file(tmp_path: Path):
    cfg = load_config(put(tmp_path, workflow="version: 3\n" + ONLY))
    assert cfg.workflow.version == 3


def test_inline_workflow_block_refused(tmp_path: Path):
    with pytest.raises(WorkflowError, match="move the workflow block"):
        load_config(put(tmp_path, FACTORY + "workflow: {start: only, steps: {}}\n"))


def test_workflow_file_errors_name_the_file(tmp_path: Path):
    path = put(tmp_path, workflow="start: [unclosed\n")
    with pytest.raises(WorkflowError, match="workflow.yaml") as exc:
        load_config(path)
    assert "\n" not in str(exc.value)
    path = put(tmp_path, workflow=ONLY.replace("next: done", "next: ghost"))
    with pytest.raises(WorkflowError, match=r"workflow.yaml: .*ghost"):
        load_config(path)


def test_write_then_read_roundtrip(tmp_path: Path):
    wf = workflow_file.read(put(tmp_path, workflow=ONLY.replace("    next:", "    description: Hello\n    next:")).with_name("workflow.yaml"))
    assert wf.steps["only"].description == "Hello"
    out = tmp_path / "out.yaml"
    workflow_file.write(out, wf.data, 4)
    again = workflow_file.read(out)
    assert again.version == 4
    assert workflow_file.data_sha(again.data) == workflow_file.data_sha(wf.data)
    assert again.steps["only"].description == "Hello"
    assert out.read_text().startswith("version: 4\n")
    assert [p.name for p in tmp_path.iterdir() if "tmp" in p.name] == []


SYNC = "mcp_servers:\n  tickets: {url: https://t/mcp, token_env: T}\n"


def test_sync_block_parsed(tmp_path: Path):
    sync = "sync:\n  ticket: {mcp_server: tickets, tool: get, args: {id: '{{ref}}'}, expect: {closed: 'False'}}\n"
    cfg = load_config(put(tmp_path, FACTORY + SYNC + sync))
    assert cfg.sync["ticket"].every_minutes == 5
    assert cfg.sync["ticket"].check.expect == {"closed": "False"}
    cfg = load_config(put(tmp_path, FACTORY + SYNC + sync.replace("expect:", "every_minutes: 9, expect:")))
    assert cfg.sync["ticket"].every_minutes == 9


def test_sync_unknown_server_refused(tmp_path: Path):
    sync = "sync:\n  ticket: {mcp_server: nope, tool: get, expect: {closed: 'False'}}\n"
    with pytest.raises(WorkflowError, match="sync.ticket.*unknown mcp_server 'nope'"):
        load_config(put(tmp_path, FACTORY + SYNC + sync))


def test_product_checkout_relative_to_factory_dir(tmp_path: Path):
    cfg = load_config(put(tmp_path, FACTORY + "product: {checkout: ../product}\n"))
    assert cfg.product_checkout == tmp_path / "../product"
    (tmp_path / "flow.yaml").write_text(ONLY)
    cfg = load_config(put(tmp_path, FACTORY + "workflow_file: flow.yaml\n"))
    assert cfg.workflow_path == tmp_path / "flow.yaml"


@pytest.mark.parametrize("version", ["~", "[1]", "true", "x", "1.5"])
def test_bad_version_names_the_file(tmp_path: Path, version: str):
    with pytest.raises(WorkflowError, match=r"workflow.yaml: version must be an integer"):
        load_config(put(tmp_path, workflow=f"version: {version}\n" + ONLY))


@pytest.mark.parametrize("sync", ["x", "{a: [1]}", "{a: {mcp_server: tickets, tool: t, expect: {c: d}, every_minutes: x}}",
                                  "{a: {mcp_server: tickets, tool: t, expect: {c: d}, every_minutes: 0}}"])
def test_bad_sync_shapes_are_workflow_errors(tmp_path: Path, sync: str):
    with pytest.raises(WorkflowError, match=r"factory.yaml: sync"):
        load_config(put(tmp_path, FACTORY + SYNC + f"sync: {sync}\n"))


def test_data_sha_ignores_formatting_and_version(tmp_path: Path):
    wf = workflow_file.read(put(tmp_path).with_name("workflow.yaml"))
    a, b = tmp_path / "a.yaml", tmp_path / "b.yaml"
    workflow_file.write(a, wf.data, 1)
    workflow_file.write(b, dict(reversed(list(wf.data.items()))), 7)
    assert workflow_file.data_sha(workflow_file.read(a).data) == workflow_file.data_sha(workflow_file.read(b).data)

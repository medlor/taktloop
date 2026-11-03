from pathlib import Path

import pytest

from taktloop.config import load_config
from taktloop.workflow import WorkflowError

WORKFLOW = """
factory: demo
auth: {issuer: http://127.0.0.1:8766, audience: taktloop}
workflow:
  start: only
  steps:
    only:
      kind: agent
      role: planner
      skill: brainstorming
      output: {artifacts: [doc]}
      next: done
"""


def test_defaults_and_relative_db(tmp_path: Path):
    path = tmp_path / "factory.yaml"
    path.write_text(WORKFLOW)
    cfg = load_config(path)
    assert cfg.factory == "demo"
    assert cfg.host == "127.0.0.1"
    assert cfg.port == 8765
    assert cfg.lease_minutes == 30
    assert cfg.database == tmp_path / ".taktloop/factory.db"
    assert cfg.url == "http://127.0.0.1:8765/mcp"
    assert cfg.workflow.start == "only"
    assert cfg.auth_issuer == "http://127.0.0.1:8766"
    assert cfg.auth_audience == "taktloop"


@pytest.mark.parametrize("auth", ["", "auth: {issuer: http://127.0.0.1:8766}\n", "auth: {audience: taktloop}\n"])
def test_auth_block_is_required(tmp_path: Path, auth: str):
    path = tmp_path / "factory.yaml"
    path.write_text(WORKFLOW.replace("auth: {issuer: http://127.0.0.1:8766, audience: taktloop}\n", auth))
    with pytest.raises(WorkflowError, match="factory.yaml: 'auth' block with issuer and audience is required"):
        load_config(path)


@pytest.mark.parametrize("line, want", [("", "off"), ("separation: off\n", "off"),
                                        ("separation: strict\n", "strict")])
def test_separation(tmp_path: Path, line: str, want: str):
    path = tmp_path / "factory.yaml"
    path.write_text(line + WORKFLOW)
    assert load_config(path).separation == want


def test_separation_rejects_other_values(tmp_path: Path):
    path = tmp_path / "factory.yaml"
    path.write_text("separation: loose\n" + WORKFLOW)
    with pytest.raises(WorkflowError, match="separation"):
        load_config(path)


CHECKED = WORKFLOW.replace("      next: done", """      next: done
      check: {mcp_server: github, tool: t, expect: {a: b}}
      evidence: [x]
      on_fail: only""")


def test_mcp_servers_load(tmp_path: Path):
    path = tmp_path / "factory.yaml"
    path.write_text(CHECKED + "mcp_servers:\n  github: {url: https://h/mcp, token_env: GH_TOK}\n")
    cfg = load_config(path)
    assert cfg.mcp_servers["github"].url == "https://h/mcp"
    assert cfg.mcp_servers["github"].token_env == "GH_TOK"
    assert cfg.env_file == tmp_path / ".env"
    path.write_text(WORKFLOW + "mcp_servers:\n  github: {url: https://h/mcp}\n")
    with pytest.raises(WorkflowError, match="token_env"):
        load_config(path)


def test_check_with_unknown_server_is_refused(tmp_path: Path):
    path = tmp_path / "factory.yaml"
    path.write_text(CHECKED)
    with pytest.raises(WorkflowError, match="step 'only': check uses unknown mcp_server 'github'"):
        load_config(path)


def test_orchestrator_block(tmp_path: Path):
    path = tmp_path / "factory.yaml"
    path.write_text(WORKFLOW)
    cfg = load_config(path)
    assert cfg.orchestrator is None
    assert cfg.root == path.parent
    path.write_text(WORKFLOW + "orchestrator: {identity: orchestrator}\n")
    orch = load_config(path).orchestrator
    assert (orch.identity, orch.tick_seconds, orch.code_retry_seconds) == ("orchestrator", 15, 60)
    assert (orch.stall_after_minutes, orch.max_lease_expiries) == (60, 2)
    path.write_text(WORKFLOW + "orchestrator: {tick_seconds: 5}\n")
    with pytest.raises(WorkflowError, match="identity"):
        load_config(path)


def test_code_step_unknown_server(tmp_path: Path):
    path = tmp_path / "factory.yaml"
    path.write_text(WORKFLOW.replace("""      kind: agent
      role: planner
      skill: brainstorming
      output: {artifacts: [doc]}""", """      kind: code
      role: verifier
      call: {mcp_server: gitlab, tool: t, evidence: {x: id}}"""))
    with pytest.raises(WorkflowError, match="gitlab"):
        load_config(path)

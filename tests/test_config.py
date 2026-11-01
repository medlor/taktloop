from pathlib import Path

from taktloop.config import load_config

WORKFLOW = """
factory: demo
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

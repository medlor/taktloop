import json
import re
from pathlib import Path

from taktloop.cli import main
from taktloop.config import load_config

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "templates/mvp/skills/taktloop/SKILL.md"
REVIEW_SKILL = ROOT / "templates/mvp/skills/taktloop-plan-review/SKILL.md"


def test_template_and_repo_factory_load():
    a = load_config(ROOT / "templates/mvp/factory.yaml")
    b = load_config(ROOT / "factory.yaml")
    assert a.workflow == b.workflow
    assert a.workflow.start == "design"
    for cfg in (a, b):
        assert (cfg.auth_issuer, cfg.auth_audience, cfg.cli_identity) == ("http://127.0.0.1:8766", "taktloop", "tobias")


def test_bridge_skill_frontmatter():
    text = SKILL.read_text()
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert m, "SKILL.md must start with YAML frontmatter"
    assert re.search(r"^name: taktloop$", m.group(1), re.M)
    assert re.search(r"^description: \S", m.group(1), re.M)
    body = text[m.end():]
    for word in ("list_open_steps", "claim_step", "get_step_context", "heartbeat",
                 "complete_step", "release_step", "claim_token"):
        assert word in body, word


def _frontmatter(text: str) -> tuple[str, str]:
    m = re.match(r"---\n(.*?)\n---\n", text, re.S)
    assert m, "SKILL.md must start with YAML frontmatter"
    return m.group(1), text[m.end():]


def test_bridge_skill_m2():
    text = SKILL.read_text()
    for word in ("TAKTLOOP_TOKEN", "feedback", ".mcp.json", "findings", "401"):
        assert word in text, word
    assert "rejection_reason" not in text


def test_plan_review_skill():
    front, body = _frontmatter(REVIEW_SKILL.read_text())
    assert re.search(r"^name: taktloop-plan-review$", front, re.M)
    assert re.search(r"^description: \S", front, re.M)
    for word in ("pass", "issues", "findings", "complete_step", "git show"):
        assert word in body, word


def test_install_skill_installs_both(tmp_path):
    assert main(["install-skill", "--dest", str(tmp_path / "D")]) == 0
    assert (tmp_path / "D/taktloop/SKILL.md").read_text() == SKILL.read_text()
    assert (tmp_path / "D/taktloop-plan-review/SKILL.md").read_text() == REVIEW_SKILL.read_text()


def test_mcp_json_reads_the_token_from_the_environment():
    assert json.loads((ROOT / ".mcp.json").read_text()) == {"mcpServers": {"taktloop": {
        "type": "http", "url": "http://127.0.0.1:8765/mcp",
        "headers": {"Authorization": "Bearer ${TAKTLOOP_TOKEN}"}}}}

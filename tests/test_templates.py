import re
from pathlib import Path

from taktloop.config import load_config

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "templates/mvp/skills/taktloop/SKILL.md"


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

import json
import re
from pathlib import Path

import yaml

from taktloop.config import load_config
from taktloop.hooks import parse_hooks

ROOT = Path(__file__).resolve().parent.parent
TEMPLATE = ROOT / "templates/mvp"
SKILL = TEMPLATE / "skills/taktloop/SKILL.md"
REVIEW_SKILL = TEMPLATE / "skills/taktloop-plan-review/SKILL.md"
TRIAGE_SKILL = TEMPLATE / "skills/taktloop-triage/SKILL.md"
BREAKOUT_SKILL = TEMPLATE / "skills/taktloop-breakout/SKILL.md"


def _template(tmp_path):
    """The template's files with __OWNER__/__REPO__ filled in, as `taktloop init` writes them."""
    for name in ("factory.yaml", "workflow.yaml", "hooks.yaml", "dev-issuer.yaml"):
        text = (TEMPLATE / name).read_text().replace("__OWNER__", "medlor").replace("__REPO__", "taktloop")
        (tmp_path / name).write_text(text)
    return tmp_path / "factory.yaml"


def test_template_loads(tmp_path):
    cfg = load_config(_template(tmp_path))
    assert (cfg.auth_issuer, cfg.auth_audience, cfg.cli_identity) == ("http://127.0.0.1:8766", "taktloop", "tobias")
    assert cfg.workflow_path == tmp_path / "workflow.yaml"
    wf, steps = cfg.workflow, cfg.workflow.steps
    assert wf.start == "open_ticket"
    assert [s for s in steps.values() if not s.description] == []
    assert "\n" not in "".join(s.description.strip() for s in steps.values())
    assert (steps["open_ticket"].kind, steps["open_ticket"].role, steps["open_ticket"].next) == ("code", "steward", "triage")
    assert dict(steps["triage"].routes) == {"quick": "workspace", "small": "plan", "project": "design",
                                            "park": "triage_decision"}
    assert (steps["triage"].dispatch, steps["triage"].push_identity) == ("push", "triage-bot")
    assert dict(steps["triage_decision"].routes) == {"quick": "workspace", "small": "plan", "project": "design",
                                                     "cancel": "cancelled"}
    assert (steps["design_approval"].approves, steps["integration_approval"].approves) == ("design", "build")
    assert steps["verify"].kind == "code"
    assert (steps["verify"].call.args["owner"], steps["verify"].call.args["repo"]) == ("medlor", "taktloop")
    assert (steps["code_review"].dispatch, steps["code_review"].push_identity) == ("push", "reviewer-bot")
    for name in ("plan", "plan_review", "build", "code_review"):
        assert "request" in steps[name].instructions, name
    assert set(cfg.mcp_servers) == {"github", "tickets"}
    assert (cfg.mcp_servers["tickets"].url, cfg.mcp_servers["tickets"].token_env) == (
        "http://127.0.0.1:8767/mcp", "TAKTLOOP_TICKETS_TOKEN")
    sync = cfg.sync["ticket"]
    assert (sync.every_minutes, sync.check.mcp_server, sync.check.tool) == (5, "tickets", "get_ticket")
    assert dict(sync.check.args) == {"id": "{{evidence.open_ticket.ticket}}"}
    assert dict(sync.check.expect) == {"closed": "False"}
    assert cfg.orchestrator.identity == "orchestrator"
    hooks = parse_hooks(yaml.safe_load((tmp_path / "hooks.yaml").read_text()), cfg.mcp_servers.keys())
    assert [(h.name, h.on) for h in hooks] == [("ticket-status", "step_opened"), ("ticket-close", "task_closed")]
    assert set(hooks[0].map) <= set(steps) and hooks[0].map["workspace"] == "in progress"
    assert hooks[1].call == ("tickets", "close_ticket", {"id": "{{evidence.open_ticket.ticket}}",
                                                         "resolution": "{{event.status}}"})


def test_template_keeps_the_owner_and_repo_tokens():
    text = (TEMPLATE / "workflow.yaml").read_text()
    assert "owner: __OWNER__" in text and "repo: __REPO__" in text and "medlor" not in text


def test_dev_issuer_roles():
    identities = yaml.safe_load((TEMPLATE / "dev-issuer.yaml").read_text())["identities"]
    for role in ("verifier", "steward", "orchestrator"):
        holders = {n for n, i in identities.items() if f"taktloop:taktloop:{role}" in i["scopes"]}
        assert holders == {"orchestrator"}, role
    assert identities["reviewer-bot"]["scopes"] == ["taktloop:taktloop:reviewer"]
    assert identities["triage-bot"]["scopes"] == ["taktloop:taktloop:triage"]
    assert identities["assistant"]["scopes"] == ["taktloop:taktloop:factory_assistant"]
    assert "taktloop:taktloop:factory_owner" in identities["tobias"]["scopes"]


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
    for word in ("TAKTLOOP_TOKEN", "feedback", ".mcp.json", "findings", "401", "taktloop override", "--to plan"):
        assert word in text, word
    assert "rejection_reason" not in text


def test_bridge_skill_m3a():
    text = SKILL.read_text()
    for word in ("check_failed", "push the branch",
                 "Never force-push", '"."'):
        assert word in text, word


def test_bridge_skill_m3b():
    text = SKILL.read_text()
    assert "work on taktloop step" in text
    assert "orchestrator" in text
    assert "gh run list --commit" not in text


def test_plan_review_skill():
    front, body = _frontmatter(REVIEW_SKILL.read_text())
    assert re.search(r"^name: taktloop-plan-review$", front, re.M)
    assert re.search(r"^description: \S", front, re.M)
    for word in ("pass", "issues", "findings", "complete_step", "git show"):
        assert word in body, word


def test_plan_review_without_a_design_doc():
    assert "request" in REVIEW_SKILL.read_text() and "no design doc" in REVIEW_SKILL.read_text()


def test_bridge_skill_m4():
    text = SKILL.read_text()
    for word in ("routes", "outcome=<route>", "findings=<reason>", "taktloop-breakout", "--config"):
        assert word in text, word


def test_triage_skill():
    front, body = _frontmatter(TRIAGE_SKILL.read_text())
    assert re.search(r"^name: taktloop-triage$", front, re.M)
    assert re.search(r"^description: \S", front, re.M)
    for word in ("quick", "small", "project", "park", "complete_step", "outcome", "findings", "Never ask"):
        assert word in body, word


def test_breakout_skill():
    front, body = _frontmatter(BREAKOUT_SKILL.read_text())
    assert re.search(r"^name: taktloop-breakout$", front, re.M)
    assert re.search(r"^description: \S", front, re.M)
    for word in ("get_workflow", "put_step", "remove_step", "set_start", "discard_draft", "approve_workflow",
                 "problems", "refused", "description", "approve", "workflow-reference.md"):
        assert word in body, word


def test_mcp_json_reads_the_token_from_the_environment():
    """This repo's own .mcp.json, as `taktloop connect` writes it (a tickets entry may be added)."""
    assert json.loads((ROOT / ".mcp.json").read_text())["mcpServers"]["taktloop"] == {
        "type": "http", "url": "http://127.0.0.1:8765/mcp",
        "headers": {"Authorization": "Bearer ${TAKTLOOP_TOKEN}"}}

import pytest

from taktloop.checks import parse_check, placeholders


def test_parse_check_stringifies_values():
    d = parse_check({"mcp_server": "github", "tool": "actions_get",
                     "args": {"resource_id": "{{evidence.ci_run}}", "page": 1},
                     "match": {"workflow_run.status": "completed"},
                     "expect": {"workflow_run.conclusion": "success"}})
    assert d.args == {"resource_id": "{{evidence.ci_run}}", "page": "1"}
    assert placeholders(d) == {"evidence.ci_run"}


def test_placeholders_cover_args_match_and_expect():
    d = parse_check({"mcp_server": "g", "tool": "t", "args": {"a": "{{ evidence.x }}"},
                     "match": {"b": "{{artifacts.change.commit}}"},
                     "expect": {"c": "{{task.id}}"}})
    assert placeholders(d) == {"evidence.x", "artifacts.change.commit", "task.id"}


@pytest.mark.parametrize("raw, msg", [
    ({"tool": "t", "expect": {"a": "b"}}, "mcp_server"),
    ({"mcp_server": "g", "expect": {"a": "b"}}, "tool"),
    ({"mcp_server": "g", "tool": "t"}, "expect"),
    ({"mcp_server": "g", "tool": "t", "expect": {"a": "b"}, "when": 1}, "unknown check field"),
    ("nope", "mapping"),
])
def test_parse_check_refuses(raw, msg):
    with pytest.raises(ValueError, match=msg):
        parse_check(raw)

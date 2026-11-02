import pytest

from taktloop.checks import (CheckUnavailable, compare, describe, parse_check, placeholders,
                             read_result, resolve)
from taktloop.models import Artifact


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


RUN = {"method": "get_workflow_run", "workflow_run": {"head_sha": "9c1e" + "0" * 36,
       "path": ".github/workflows/ci.yml", "status": "in_progress", "conclusion": None, "id": 12345}}


def test_resolve_substitutes_evidence_and_artifacts():
    d = parse_check({"mcp_server": "g", "tool": "t", "args": {"resource_id": "{{evidence.ci_run}}"},
                     "match": {"workflow_run.head_sha": "{{ artifacts.change.commit }}"},
                     "expect": {"workflow_run.conclusion": "success"}})
    art = Artifact(name="change", path="p", commit="abc", branch="b", step="s", round=1)
    r = resolve(d, {"ci_run": "12345"}, {"change": art})
    assert r.args == {"resource_id": "12345"}
    assert r.match == {"workflow_run.head_sha": "abc"}
    assert r.expect == d.expect


def test_compare_reports_each_mismatch():
    ms = compare({"workflow_run.status": "completed", "workflow_run.path": ".github/workflows/ci.yml"}, RUN)
    assert [describe(m) for m in ms] == ["workflow_run.status is in_progress, expected completed"]


def test_compare_missing_field():
    assert describe(compare({"workflow_run.nope": "x"}, RUN)[0]) == "workflow_run.nope is missing, expected x"


def test_compare_stringifies_numbers():
    assert compare({"workflow_run.id": "12345"}, RUN) == []


def test_read_result_prefers_structured_then_text_then_refuses():
    assert read_result({"a": 1}, ["garbage"]) == {"a": 1}
    assert read_result(None, ['{"a": 1}']) == {"a": 1}
    for bad in ([], ["not json"], ["[1, 2]"]):
        with pytest.raises(CheckUnavailable):
            read_result(None, bad)

import pytest

from taktloop.checks import (CheckUnavailable, compare, describe, lookup, parse_call, parse_check,
                             placeholders, read_result, resolve, substitute)
from taktloop.models import Artifact


def test_parse_check_keeps_nested_args():
    d = parse_check({"mcp_server": "github", "tool": "actions_list",
                     "args": {"page": 1, "workflow_runs_filter": {"branch": "{{artifacts.change.branch}}"}},
                     "expect": {"x": "y"}})
    assert d.args == {"page": 1, "workflow_runs_filter": {"branch": "{{artifacts.change.branch}}"}}
    assert placeholders(d) == {"artifacts.change.branch"}


def test_parse_call():
    c = parse_call({"mcp_server": "github", "tool": "actions_list", "args": {"a": "b"},
                    "evidence": {"ci_run": "workflow_runs.workflow_runs.0.id"}})
    assert c.evidence == {"ci_run": "workflow_runs.workflow_runs.0.id"}
    for bad in ({"tool": "t", "evidence": {"k": "p"}}, {"mcp_server": "g", "tool": "t"},
                {"mcp_server": "g", "tool": "t", "evidence": {"k": "p"}, "expect": {}}):
        with pytest.raises(ValueError):
            parse_call(bad)


def test_substitute_nested():
    arts = {"change": Artifact(name="change", path=".", commit="c" * 40, branch="taktloop/1-x",
                               step="build", round=1)}
    assert substitute({"f": {"branch": "{{artifacts.change.branch}}"}, "n": 3, "l": ["{{artifacts.change.commit}}"]},
                      {}, arts) == {"f": {"branch": "taktloop/1-x"}, "n": 3, "l": ["c" * 40]}


def test_lookup_indexes_lists():
    r = {"workflow_runs": {"workflow_runs": [{"id": 7}, {"id": 6}]}}
    assert lookup(r, "workflow_runs.workflow_runs.0.id") == "7"
    assert lookup(r, "workflow_runs.workflow_runs.5.id") is None
    assert lookup({"workflow_runs": {"workflow_runs": []}}, "workflow_runs.workflow_runs.0.id") is None


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


def test_substitute_values_win():
    assert substitute({"t": "{{task.title}}", "i": "{{evidence.open_ticket.ticket}}"}, {}, {},
                      {"task.title": "Add X", "evidence.open_ticket.ticket": "4"}) == {"t": "Add X", "i": "4"}

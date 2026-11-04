import os
import socket
from pathlib import Path

import pytest

from taktloop.checks import CheckUnavailable
from taktloop.external import ExternalServer, credential, make_checker, read_dotenv

ARGS = {"method": "get_workflow_run", "owner": "o", "repo": "r", "resource_id": "12345"}


def test_read_dotenv_formats(tmp_path: Path):
    f = tmp_path / ".env"
    f.write_text("# c\n\nA=1\nexport B=\"two\"\nC='3'\nD=x=y\nE = pad  \nF=  'q'  \n")
    assert read_dotenv(f) == {"A": "1", "B": "two", "C": "3", "D": "x=y", "E": "pad", "F": "q"}
    assert read_dotenv(tmp_path / "missing") == {}


def test_credential_env_wins_then_dotenv_then_none():
    server = ExternalServer("github", "http://x", "TOK")
    assert credential(server, {"TOK": "env"}, {"TOK": "file"}) == "env"
    assert credential(server, {"TOK": ""}, {"TOK": "file"}) == "file"
    assert credential(server, {}, {}) is None


def _checker(url, environ=None, timeout=30.0):
    servers = {"github": ExternalServer("github", url, "TAKTLOOP_GITHUB_TOKEN")}
    return make_checker(servers, {"TAKTLOOP_GITHUB_TOKEN": "s3cret"} if environ is None else environ,
                        {}, timeout)


def test_external_checker_calls_tool(fake_github):
    url, calls = fake_github
    result = _checker(url)("github", "actions_get", ARGS)
    assert result == {"method": "get_workflow_run", "workflow_run": {"id": 12345, "status": "completed"}}
    assert calls[-1]["token"] == "Bearer s3cret"
    assert calls[-1]["resource_id"] == "12345"


def test_external_tool_error_and_unreachable(fake_github):
    url, _ = fake_github
    with pytest.raises(CheckUnavailable, match="404 Not Found"):
        _checker(url)("github", "actions_get", {**ARGS, "resource_id": "999"})
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        closed = s.getsockname()[1]
    with pytest.raises(CheckUnavailable, match="github is unreachable: ConnectError") as e:
        _checker(f"http://127.0.0.1:{closed}/mcp")("github", "actions_get", ARGS)
    assert "s3cret" not in str(e.value)
    with pytest.raises(CheckUnavailable) as e:
        _checker(url, environ={"TAKTLOOP_GITHUB_TOKEN": "bad-token"})("github", "actions_get", ARGS)
    assert str(e.value) == "github answered 401"
    with pytest.raises(CheckUnavailable) as e:
        _checker(url, environ={})("github", "actions_get", ARGS)
    assert str(e.value) == "the core has no credential for github (TAKTLOOP_GITHUB_TOKEN is not set)"


def test_external_timeout(fake_github):
    url, _ = fake_github
    with pytest.raises(CheckUnavailable, match="github did not answer within"):
        _checker(url, timeout=0.2)("github", "sleepy", {"seconds": "1"})


def test_external_checker_closes_its_http_client(fake_github, monkeypatch):
    import taktloop.external as external

    made, real = [], external.create_mcp_http_client
    monkeypatch.setattr(external, "create_mcp_http_client", lambda **kw: made.append(real(**kw)) or made[-1])
    _checker(fake_github[0])("github", "actions_get", ARGS)
    assert made and all(c.is_closed for c in made)


ROOT = Path(__file__).resolve().parent.parent
LIVE_URL = "https://api.githubcopilot.com/mcp/x/actions/readonly"


def _live_environ() -> dict[str, str]:
    env = {k: v for k, v in os.environ.items() if k == "TAKTLOOP_GITHUB_TOKEN"}
    return env if env.get("TAKTLOOP_GITHUB_TOKEN") else read_dotenv(ROOT / ".env")


@pytest.mark.skipif(not _live_environ().get("TAKTLOOP_GITHUB_TOKEN"),
                    reason="needs TAKTLOOP_GITHUB_TOKEN in the environment or .env")
def test_live_github_run():
    """GitHub's real MCP server: tool name, argument types and field names on a known run."""
    checker = make_checker({"github": ExternalServer("github", LIVE_URL, "TAKTLOOP_GITHUB_TOKEN")},
                           _live_environ(), {})
    result = checker("github", "actions_get", {"method": "get_workflow_run", "owner": "medlor",
                                               "repo": "taktloop", "resource_id": "41508346956"})
    run = result["workflow_run"]
    assert (run["head_sha"], run["path"], run["conclusion"]) == (
        "b4c593058b64762308b72f0225f63ccb12b07b98", ".github/workflows/ci.yml", "success")
    listed = checker("github", "actions_list", {
        "method": "list_workflow_runs", "owner": "medlor", "repo": "taktloop", "resource_id": "ci.yml",
        "workflow_runs_filter": {"branch": "main"}})
    assert listed["workflow_runs"]["workflow_runs"][0]["head_branch"] == "main"


def test_tool_lister_lists_fake_server(fake_github):
    from taktloop.external import make_tool_lister
    url, _ = fake_github
    servers = {"github": ExternalServer("github", url, "TAKTLOOP_GITHUB_TOKEN")}
    assert {"actions_get", "actions_list"} <= make_tool_lister(servers, {"TAKTLOOP_GITHUB_TOKEN": "t"}, {})("github")
    with pytest.raises(CheckUnavailable, match="no credential for github"):
        make_tool_lister(servers, {}, {})("github")


def test_unconfigured_server_is_unavailable_not_a_keyerror():
    from taktloop.external import make_tool_lister
    with pytest.raises(CheckUnavailable, match="nope is not configured"):
        make_tool_lister({}, {}, {})("nope")
    with pytest.raises(CheckUnavailable, match="nope is not configured"):
        make_checker({}, {}, {})("nope", "t", {})


def test_non_text_content_is_not_an_attribute_error(monkeypatch):
    import taktloop.external as external
    from types import SimpleNamespace

    from taktloop.checks import ToolFailed
    image = SimpleNamespace(type="image", data="", mimeType="image/png")  # no .text
    result = SimpleNamespace(is_error=True, content=[image], structured_content=None)
    monkeypatch.setattr(external, "_remote", lambda *a: lambda name, action: result)
    check = make_checker({}, {}, {})
    with pytest.raises(ToolFailed, match="unknown error"):
        check("github", "t", {})
    result.is_error = False
    with pytest.raises(CheckUnavailable, match="not a JSON object"):
        check("github", "t", {})

import json

import jwt
import pytest

from taktloop import token_client
from taktloop.token_client import TokenError, fetch_token


def test_fetch_token_returns_a_jwt_for_the_identity(real_issuer):
    url, creds = real_issuer
    claims = jwt.decode(fetch_token("agent-a", creds), options={"verify_signature": False})
    assert claims["sub"] == "agent-a" and claims["iss"] == url


def test_missing_credentials_name_the_dir(tmp_path):
    with pytest.raises(TokenError, match=f"no credentials for ghost in {tmp_path}; run taktloop issuer init "
                       r"\(or taktloop issuer init --rotate ghost if the file was deleted\)"):
        fetch_token("ghost", tmp_path)


def test_wrong_secret_is_refused(real_issuer):
    _, creds = real_issuer
    path = creds / "agent-a.json"
    path.write_text(json.dumps(json.loads(path.read_text()) | {"client_secret": "wrong"}))
    with pytest.raises(TokenError, match="401|400"):
        fetch_token("agent-a", creds)


def test_unreachable_issuer(tmp_path):
    (tmp_path / "x.json").write_text(json.dumps({"issuer": "http://127.0.0.1:1", "client_id": "x", "client_secret": "s"}))
    with pytest.raises(TokenError, match="not reachable"):
        fetch_token("x", tmp_path)


@pytest.mark.parametrize("content", ['{"issuer": "x"}', "[1, 2]", "not json", '{"issuer": 1, "client_id": "x", '
                                     '"client_secret": "s"}'])
def test_malformed_credentials_are_a_token_error(tmp_path, content):
    (tmp_path / "x.json").write_text(content)
    with pytest.raises(TokenError, match=f"{tmp_path / 'x.json'}"):
        fetch_token("x", tmp_path)


@pytest.mark.parametrize("answers", [[{}], [[1]], [{"token_endpoint": "http://t"}, {"no": "token"}],
                                     [{"token_endpoint": "http://t"}, ["x"]]])
def test_malformed_issuer_answers_are_a_token_error(tmp_path, monkeypatch, answers):
    (tmp_path / "x.json").write_text(json.dumps({"issuer": "http://i", "client_id": "x", "client_secret": "s"}))
    it = iter(answers)
    monkeypatch.setattr(token_client, "_open", lambda req: next(it))
    with pytest.raises(TokenError, match="unexpected answer from the issuer"):
        fetch_token("x", tmp_path)

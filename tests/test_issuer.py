import base64
import json
import stat
from datetime import timedelta
from pathlib import Path

import httpx2
import jwt
import pytest

from taktloop.issuer.app import build_app
from taktloop.issuer.config import IssuerConfig, load_issuer_config
from taktloop.issuer.state import init_state

ISS = "http://127.0.0.1:8766"
ROLES = ("taktloop:taktloop:planner", "taktloop:taktloop:reviewer")


@pytest.fixture
def anyio_backend():
    return "asyncio"


def make_cfg(tmp_path: Path) -> IssuerConfig:
    (tmp_path / "dev-issuer.yaml").write_text(
        f"issuer: {ISS}\naudience: taktloop\ntoken_lifetime: 12h\nstate_dir: state\n"
        f"identities:\n  agent-a: {{scopes: [{', '.join(ROLES)}]}}\n  tobias: {{scopes: [taktloop:taktloop:approver]}}\n"
    )
    return load_issuer_config(tmp_path / "dev-issuer.yaml")


def jwks_key(client):
    """Public key from the issuer's own JWKS document (async client: fetched via the cache below)."""
    return client.key


@pytest.fixture
def creds(tmp_path):
    cfg = make_cfg(tmp_path)
    init_state(cfg, tmp_path / "creds")
    return {p.stem: json.loads(p.read_text())["client_secret"] for p in (tmp_path / "creds").glob("*.json")}


@pytest.fixture
async def issuer(tmp_path, creds):
    cfg = load_issuer_config(tmp_path / "dev-issuer.yaml")
    transport = httpx2.ASGITransport(app=build_app(cfg))
    async with httpx2.AsyncClient(transport=transport, base_url="http://test") as c:
        jwk = (await c.get("/jwks.json")).json()["keys"][0]
        c.key = jwt.PyJWK(jwk).key
        yield c


async def token(c, creds, **over):
    form = {"grant_type": "client_credentials", "client_id": "agent-a", "client_secret": creds["agent-a"]} | over
    return await c.post("/token", data=form)


@pytest.mark.anyio
async def test_metadata(issuer):
    m = (await issuer.get("/.well-known/oauth-authorization-server")).json()
    assert m["issuer"] == ISS
    assert m["token_endpoint"] == f"{ISS}/token"
    assert m["jwks_uri"] == f"{ISS}/jwks.json"
    assert m["grant_types_supported"] == ["client_credentials"]


@pytest.mark.anyio
async def test_token_carries_all_scopes(issuer, creds):
    r = await token(issuer, creds)
    assert r.status_code == 200 and r.json()["token_type"] == "Bearer"
    assert r.json()["expires_in"] == 12 * 3600
    claims = jwt.decode(r.json()["access_token"], key=jwks_key(issuer), algorithms=["ES256"], audience="taktloop")
    assert claims["sub"] == "agent-a" and claims["client_id"] == "agent-a" and claims["iss"] == ISS
    assert claims["scope"] == " ".join(ROLES)
    assert claims["exp"] - claims["iat"] == 12 * 3600
    kid = jwt.get_unverified_header(r.json()["access_token"])["kid"]
    assert kid == (await issuer.get("/jwks.json")).json()["keys"][0]["kid"]


@pytest.mark.anyio
async def test_basic_auth_accepted(issuer, creds):
    auth = base64.b64encode(f"agent-a:{creds['agent-a']}".encode()).decode()
    r = await issuer.post("/token", data={"grant_type": "client_credentials"},
                          headers={"Authorization": f"Basic {auth}"})
    assert r.status_code == 200


@pytest.mark.anyio
async def test_wrong_secret_is_invalid_client(issuer):
    r = await issuer.post("/token", data={"grant_type": "client_credentials",
                                          "client_id": "agent-a", "client_secret": "nope"})
    assert r.status_code == 401 and r.json() == {"error": "invalid_client"}


@pytest.mark.anyio
async def test_unknown_identity_is_invalid_client(issuer, creds):
    r = await token(issuer, creds, client_id="mallory")
    assert r.status_code == 401 and r.json() == {"error": "invalid_client"}


@pytest.mark.anyio
async def test_other_grant_is_unsupported(issuer, creds):
    r = await token(issuer, creds, grant_type="password")
    assert r.status_code == 400 and r.json() == {"error": "unsupported_grant_type"}


def secret_of(creds_dir, ident):
    return json.loads((creds_dir / f"{ident}.json").read_text())["client_secret"]


def test_init_keeps_existing_secrets_and_rotates_one(tmp_path):
    cfg, creds = make_cfg(tmp_path), tmp_path / "creds"
    assert sorted(init_state(cfg, creds)) == ["agent-a", "tobias"]
    before = secret_of(creds, "agent-a")
    assert init_state(cfg, creds) == []
    assert secret_of(creds, "agent-a") == before
    assert init_state(cfg, creds, rotate="agent-a") == ["agent-a"]
    assert secret_of(creds, "agent-a") != before
    assert json.loads((creds / "agent-a.json").read_text())["issuer"] == ISS


def test_credentials_file_mode_is_0600(tmp_path):
    init_state(make_cfg(tmp_path), tmp_path / "creds")
    assert stat.S_IMODE((tmp_path / "creds" / "tobias.json").stat().st_mode) == 0o600


def test_rotate_unknown_identity_raises(tmp_path):
    with pytest.raises(ValueError, match="mallory"):
        init_state(make_cfg(tmp_path), tmp_path / "creds", rotate="mallory")


def test_config_parsing(tmp_path):
    cfg = make_cfg(tmp_path)
    assert cfg.token_lifetime == timedelta(hours=12)
    assert cfg.state_dir == tmp_path / "state"
    assert cfg.identities["agent-a"] == ROLES
    committed = load_issuer_config(Path(__file__).resolve().parents[1] / "dev-issuer.yaml")
    assert set(committed.identities) == {"tobias", "agent-a", "agent-b", "orchestrator", "reviewer-bot"}


def test_cli_issuer_init(tmp_path, capsys):
    from taktloop import cli

    make_cfg(tmp_path)
    cfg, creds = tmp_path / "dev-issuer.yaml", tmp_path / "creds"
    assert cli.main(["issuer", "init", "--config", str(cfg), "--credentials", str(creds)]) == 0
    assert "agent-a" in capsys.readouterr().out and (creds / "tobias.json").exists()
    assert cli.main(["issuer", "init", "--config", str(cfg), "--credentials", str(creds), "--rotate", "x"]) == 2

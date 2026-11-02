import logging
import time

import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import ec, rsa

from taktloop.auth import JwtVerifier, roles_from_scopes

ISSUER = "http://issuer.test"
META = f"{ISSUER}/.well-known/oauth-authorization-server"
JWKS = f"{ISSUER}/jwks.json"

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _jwk(key, kid):
    return jwt.algorithms.ECAlgorithm.to_jwk(key.public_key(), as_dict=True) | {"kid": kid, "alg": "ES256"}


class Issuer:
    """An in-memory issuer: metadata and JWKS served through fetch_json, counting fetches."""

    def __init__(self):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.keys = [_jwk(self.key, "k1")]
        self.fetches: list[str] = []

    def fetch_json(self, url):
        self.fetches.append(url)
        return {META: {"issuer": ISSUER, "jwks_uri": JWKS}, JWKS: {"keys": list(self.keys)}}[url]

    def token(self, key=None, kid="k1", algorithm="ES256", **over):
        now = int(time.time())
        claims = {"iss": ISSUER, "sub": "agent-a", "aud": "taktloop", "iat": now, "exp": now + 60,
                  "scope": "taktloop:taktloop:planner openid", "client_id": "agent-a"} | over
        return jwt.encode(claims, key or self.key, algorithm=algorithm, headers={"kid": kid})


@pytest.fixture
def issuer():
    return Issuer()


@pytest.fixture
def verifier(issuer):
    return JwtVerifier(ISSUER, "taktloop", fetch_json=issuer.fetch_json)


def test_roles_from_scopes_only_this_factory():
    scopes = ["taktloop:taktloop:planner", "taktloop:other:approver", "openid"]
    assert roles_from_scopes(scopes, "taktloop") == {"planner"}


def test_other_factory_scopes_give_no_roles():
    """Review Focus 1: a valid token for another factory carries no roles here."""
    assert roles_from_scopes(["taktloop:other:approver", "taktloop:other:planner"], "taktloop") == frozenset()


async def test_valid_token(issuer, verifier):
    tok = issuer.token()
    at = await verifier.verify_token(tok)
    assert at is not None
    assert at.subject == "agent-a" and at.client_id == "agent-a"
    assert at.scopes == ["taktloop:taktloop:planner", "openid"]
    assert at.claims == {"iss": ISSUER} and at.token == tok and at.expires_at > time.time()
    assert issuer.fetches == [META, JWKS]
    await verifier.verify_token(issuer.token())
    assert issuer.fetches == [META, JWKS]  # keys are cached


@pytest.mark.parametrize("bad", [
    {"key": ec.generate_private_key(ec.SECP256R1())},
    {"iss": "http://elsewhere.test"},
    {"aud": "other"},
    {"exp": int(time.time()) - 10},
    {"kid": "nope"},
], ids=["signature", "iss", "aud", "expired", "unknown-kid"])
async def test_invalid_tokens_are_refused_and_logged(issuer, verifier, bad, caplog):
    with caplog.at_level(logging.WARNING, logger="taktloop.auth"):
        assert await verifier.verify_token(issuer.token(**bad)) is None
    assert [r.name for r in caplog.records] == ["taktloop.auth"]


async def test_garbage_and_unreachable_issuer_are_refused(issuer):
    def down(url):
        raise OSError("connection refused")

    assert await JwtVerifier(ISSUER, "taktloop", fetch_json=issuer.fetch_json).verify_token("x.y.z") is None
    assert await JwtVerifier(ISSUER, "taktloop", fetch_json=down).verify_token(issuer.token()) is None


async def test_unknown_kid_refetches_once(issuer, verifier):
    assert await verifier.verify_token(issuer.token()) is not None
    rotated = ec.generate_private_key(ec.SECP256R1())
    issuer.keys.append(_jwk(rotated, "k2"))
    assert await verifier.verify_token(issuer.token(key=rotated, kid="k2")) is not None
    assert issuer.fetches == [META, JWKS, JWKS]
    assert await verifier.verify_token(issuer.token(kid="k3")) is None
    assert issuer.fetches == [META, JWKS, JWKS, JWKS]


async def test_rs256_issuer_is_accepted(issuer, verifier):
    """Spec 5.1: any OIDC/OAuth issuer works; Okta and Keycloak sign RS256 by default."""
    rsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    issuer.keys = [jwt.algorithms.RSAAlgorithm.to_jwk(rsa_key.public_key(), as_dict=True) | {"kid": "r1"}]
    tok = issuer.token(key=rsa_key, kid="r1", algorithm="RS256")
    assert (await verifier.verify_token(tok)).subject == "agent-a"
    assert await verifier.verify_token(issuer.token(key=rsa_key, kid="r1", algorithm="RS512")) is None


async def test_symmetric_keys_are_refused(issuer, verifier, caplog):
    secret = "s" * 32
    issuer.keys = [{"kty": "oct", "k": jwt.utils.base64url_encode(secret.encode()).decode(), "kid": "h1",
                    "alg": "HS256"}]
    with caplog.at_level(logging.WARNING, logger="taktloop.auth"):
        assert await verifier.verify_token(issuer.token(key=secret, kid="h1", algorithm="HS256")) is None
    assert "symmetric" in caplog.text


async def test_unsigned_token_for_a_known_key_is_refused(issuer, verifier, caplog):
    """kid k1 is the issuer's real ES256 key, so the refusal is the `none` algorithm itself."""
    unsigned = jwt.encode({"iss": ISSUER, "sub": "x", "aud": "taktloop", "exp": int(time.time()) + 60},
                          None, algorithm="none", headers={"kid": "k1"})
    with caplog.at_level(logging.WARNING, logger="taktloop.auth"):
        assert await verifier.verify_token(unsigned) is None
    assert "unknown key id" not in caplog.text and "alg" in caplog.text.lower()


@pytest.mark.parametrize("configured,claimed", [(ISSUER + "/", ISSUER), (ISSUER, ISSUER + "/")])
async def test_trailing_slash_in_issuer_is_ignored(issuer, configured, claimed):
    v = JwtVerifier(configured, "taktloop", fetch_json=issuer.fetch_json)
    assert await v.verify_token(issuer.token(iss=claimed)) is not None


async def test_falls_back_to_openid_configuration(issuer):
    oidc = f"{ISSUER}/.well-known/openid-configuration"

    def fetch(url):
        if url == META:
            raise OSError("404")
        return issuer.fetch_json(META if url == oidc else url)

    assert await JwtVerifier(ISSUER, "taktloop", fetch_json=fetch).verify_token(issuer.token()) is not None

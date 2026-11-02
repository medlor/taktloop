"""The issuer's HTTP app: RFC 8414 metadata, JWKS and the client-credentials token endpoint."""

import base64
import binascii
import hashlib
import hmac
import json
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import unquote

import jwt
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from taktloop.issuer.config import IssuerConfig
from taktloop.issuer.state import hash_secret, load_secret_hashes, load_signing_key


def _now() -> datetime:
    return datetime.now(UTC)


def _client_credentials(request: Request, form) -> tuple[str, str]:
    auth = request.headers.get("authorization", "")
    if auth.lower().startswith("basic "):
        try:
            user, _, pw = base64.b64decode(auth[6:]).decode().partition(":")
            return unquote(user), unquote(pw)
        except (binascii.Error, UnicodeDecodeError):
            return "", ""
    return str(form.get("client_id", "")), str(form.get("client_secret", ""))


def build_app(cfg: IssuerConfig, clock: Callable[[], datetime] = _now) -> Starlette:
    key = load_signing_key(cfg.state_dir)
    public = jwt.algorithms.ECAlgorithm.to_jwk(key.public_key(), as_dict=True)
    kid = _thumbprint(public)
    public |= {"kid": kid, "use": "sig", "alg": "ES256"}

    async def metadata(request: Request) -> JSONResponse:
        return JSONResponse({
            "issuer": cfg.issuer,
            "token_endpoint": f"{cfg.issuer}/token",
            "jwks_uri": f"{cfg.issuer}/jwks.json",
            "grant_types_supported": ["client_credentials"],
        })

    async def jwks(request: Request) -> JSONResponse:
        return JSONResponse({"keys": [public]})

    async def token(request: Request) -> JSONResponse:
        form = await request.form()
        if form.get("grant_type") != "client_credentials":
            return JSONResponse({"error": "unsupported_grant_type"}, status_code=400)
        client_id, secret = _client_credentials(request, form)
        expected = load_secret_hashes(cfg.state_dir).get(client_id, "")
        known = client_id in cfg.identities and bool(expected)
        if not (hmac.compare_digest(hash_secret(secret), expected or "0") and known):
            return JSONResponse({"error": "invalid_client"}, status_code=401,
                                headers={"WWW-Authenticate": "Basic"})
        iat = int(clock().timestamp())
        lifetime = int(cfg.token_lifetime.total_seconds())
        claims = {"iss": cfg.issuer, "sub": client_id, "aud": cfg.audience, "iat": iat,
                  "exp": iat + lifetime, "scope": " ".join(cfg.identities[client_id]),
                  "client_id": client_id}
        access = jwt.encode(claims, key, algorithm="ES256", headers={"kid": kid})
        return JSONResponse({"access_token": access, "token_type": "Bearer", "expires_in": lifetime},
                            headers={"Cache-Control": "no-store"})

    return Starlette(routes=[
        Route("/.well-known/oauth-authorization-server", metadata),
        Route("/jwks.json", jwks),
        Route("/token", token, methods=["POST"]),
    ])


def _thumbprint(jwk: dict) -> str:
    """RFC 7638 JWK thumbprint (SHA-256, base64url) of an EC public key."""
    canonical = json.dumps({k: jwk[k] for k in ("crv", "kty", "x", "y")}, separators=(",", ":"), sort_keys=True)
    return base64.urlsafe_b64encode(hashlib.sha256(canonical.encode()).digest()).rstrip(b"=").decode()

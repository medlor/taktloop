"""Validate the bearer tokens of the configured issuer and map their scopes to roles.

The issuer is reached over HTTP only: its RFC 8414 metadata names the JWKS.
"""

import json
import logging
import urllib.request
from collections.abc import Callable, Iterable

import anyio
import jwt
from mcp.server.auth.provider import AccessToken

log = logging.getLogger("taktloop.auth")


def _get_json(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=5) as r:
        return json.load(r)


def roles_from_scopes(scopes: Iterable[str], factory: str) -> frozenset[str]:
    """Roles of the `taktloop:<factory>:<role>` scopes; other factories' scopes are ignored."""
    prefix = f"taktloop:{factory}:"
    return frozenset(s[len(prefix):] for s in scopes if s.startswith(prefix) and len(s) > len(prefix))


class JwtVerifier:
    """mcp's TokenVerifier for ES256 JWTs: signature, `iss`, `aud` and `exp` are checked."""

    def __init__(self, issuer: str, audience: str, fetch_json: Callable[[str], dict] = _get_json):
        self.issuer, self.audience, self._fetch = issuer, audience, fetch_json
        self._jwks_uri: str | None = None
        self._keys: dict[str, jwt.PyJWK] = {}

    def _refetch_keys(self) -> None:
        if self._jwks_uri is None:
            meta = self._fetch(f"{self.issuer.rstrip('/')}/.well-known/oauth-authorization-server")
            self._jwks_uri = meta["jwks_uri"]
        self._keys = {k.key_id: k for k in jwt.PyJWKSet.from_dict(self._fetch(self._jwks_uri)).keys}

    async def _key(self, kid: str | None) -> jwt.PyJWK:
        if kid not in self._keys:  # first use, or the issuer rotated its key: fetch once
            await anyio.to_thread.run_sync(self._refetch_keys)
        if kid not in self._keys:
            raise jwt.InvalidKeyError(f"unknown key id {kid!r}")
        return self._keys[kid]

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            key = await self._key(jwt.get_unverified_header(token).get("kid"))
            claims = jwt.decode(token, key, algorithms=["ES256"], audience=self.audience, issuer=self.issuer,
                                options={"require": ["iss", "sub", "aud", "exp"]})
        except Exception as e:  # any failure is a refused token; the SDK answers 401
            log.warning("bearer token refused: %s", e)
            return None
        return AccessToken(token=token, client_id=str(claims.get("client_id") or claims["sub"]),
                           scopes=str(claims.get("scope", "")).split(), expires_at=int(claims["exp"]),
                           subject=claims["sub"], claims={"iss": claims["iss"]})

"""Validate the bearer tokens of the configured issuer and map their scopes to roles.

The issuer is reached over HTTP only: its RFC 8414 metadata (or OIDC discovery) names the JWKS.
"""

import json
import logging
import urllib.request
from collections.abc import Callable, Iterable

import anyio
import jwt
from jwt.algorithms import ECAlgorithm, OKPAlgorithm, RSAAlgorithm
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
    """mcp's TokenVerifier for JWTs signed with the issuer's asymmetric JWKS keys (RS*, PS*, ES*,
    EdDSA; the algorithm comes from the key, never HS* or none): signature, `iss`, `aud`, `exp`."""

    def __init__(self, issuer: str, audience: str, fetch_json: Callable[[str], dict] = _get_json):
        self.issuer, self.audience, self._fetch = issuer.rstrip("/"), audience, fetch_json
        self._jwks_uri: str | None = None
        self._keys: dict[str, jwt.PyJWK] = {}

    def _refetch_keys(self) -> None:
        if self._jwks_uri is None:
            try:
                meta = self._fetch(f"{self.issuer}/.well-known/oauth-authorization-server")
            except Exception:  # an OIDC-only issuer: fall back to its discovery document
                meta = self._fetch(f"{self.issuer}/.well-known/openid-configuration")
            self._jwks_uri = meta["jwks_uri"]
        self._keys = {k.key_id: k for k in jwt.PyJWKSet.from_dict(self._fetch(self._jwks_uri)).keys}

    async def _key(self, kid: str | None) -> jwt.PyJWK:
        if kid not in self._keys:  # first use, or the issuer rotated its key: fetch once
            await anyio.to_thread.run_sync(self._refetch_keys)
        if kid not in self._keys:
            raise jwt.InvalidKeyError(f"unknown key id {kid!r}")
        key = self._keys[kid]
        if not isinstance(key.Algorithm, (RSAAlgorithm, ECAlgorithm, OKPAlgorithm)):
            raise jwt.InvalidKeyError(f"key {kid!r} is symmetric ({key.algorithm_name}); "
                                      "issuer keys must be asymmetric")
        return key

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            key = await self._key(jwt.get_unverified_header(token).get("kid"))
            claims = jwt.decode(token, key, algorithms=[key.algorithm_name], audience=self.audience,
                                issuer=[self.issuer, self.issuer + "/"],
                                options={"require": ["iss", "sub", "aud", "exp"]})
        except Exception as e:  # any failure is a refused token; the SDK answers 401
            log.warning("bearer token refused: %s", e)
            return None
        return AccessToken(token=token, client_id=str(claims.get("client_id") or claims["sub"]),
                           scopes=str(claims.get("scope", "")).split(), expires_at=int(claims["exp"]),
                           subject=claims["sub"], claims={"iss": claims["iss"]})

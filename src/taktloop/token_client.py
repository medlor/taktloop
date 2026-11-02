"""Fetch a dev-issuer token with the client-credentials grant (stdlib only)."""

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class TokenError(Exception):
    """No token could be obtained; the message says why."""


def _open(req: urllib.request.Request) -> dict:
    try:
        with urllib.request.urlopen(req, timeout=5) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise TokenError(f"issuer answered {e.code} at {req.full_url}") from None
    except (OSError, ValueError) as e:
        raise TokenError(f"issuer not reachable at {req.full_url}: {e}") from None


def _field(data, key: str, where: str) -> str:
    value = data.get(key) if isinstance(data, dict) else None
    if not isinstance(value, str) or not value:
        raise TokenError(f"{where}: missing or invalid {key!r}")
    return value


def fetch_token(identity: str, credentials_dir: Path = Path(".taktloop/credentials")) -> str:
    path = Path(credentials_dir) / f"{identity}.json"
    try:
        creds = json.loads(path.read_text())
    except FileNotFoundError:
        raise TokenError(f"no credentials for {identity} in {credentials_dir}; run taktloop issuer init (or "
                         f"taktloop issuer init --rotate {identity} if the file was deleted)") from None
    except (OSError, ValueError) as e:
        raise TokenError(f"{path}: {e}") from None
    issuer, client_id, secret = (_field(creds, k, str(path)) for k in ("issuer", "client_id", "client_secret"))
    meta_url = f"{issuer.rstrip('/')}/.well-known/oauth-authorization-server"
    endpoint = _field(_open(urllib.request.Request(meta_url)), "token_endpoint",
                      f"unexpected answer from the issuer at {meta_url}")
    form = urllib.parse.urlencode({
        "grant_type": "client_credentials", "client_id": client_id, "client_secret": secret,
    }).encode()
    return _field(_open(urllib.request.Request(endpoint, data=form)), "access_token",
                  f"unexpected answer from the issuer at {endpoint}")

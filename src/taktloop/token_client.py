"""Fetch a dev-issuer token with the client-credentials grant (stdlib only)."""

import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


class TokenError(Exception):
    """No token could be obtained; the message says why."""


def _open(url: str, data: bytes | None = None) -> dict:
    try:
        with urllib.request.urlopen(urllib.request.Request(url, data=data), timeout=5) as r:
            return json.load(r)
    except urllib.error.HTTPError as e:
        raise TokenError(f"issuer answered {e.code} at {url}") from None
    except (OSError, ValueError) as e:  # ValueError also covers a URL urllib cannot parse
        raise TokenError(f"issuer not reachable at {url}: {e}") from None


def _http_url(value: str, where: str) -> str:
    parts = urllib.parse.urlsplit(value)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise TokenError(f"{where}: {value!r} is not an http(s) URL (e.g. http://127.0.0.1:8766)")
    return value


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
    meta_url = f"{_http_url(issuer, f'{path}: issuer').rstrip('/')}/.well-known/oauth-authorization-server"
    where = f"unexpected answer from the issuer at {meta_url}"
    endpoint = _http_url(_field(_open(meta_url), "token_endpoint", where), f"{where}: token_endpoint")
    form = urllib.parse.urlencode({
        "grant_type": "client_credentials", "client_id": client_id, "client_secret": secret,
    }).encode()
    return _field(_open(endpoint, data=form), "access_token",
                  f"unexpected answer from the issuer at {endpoint}")

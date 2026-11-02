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


def fetch_token(identity: str, credentials_dir: Path = Path(".taktloop/credentials")) -> str:
    path = Path(credentials_dir) / f"{identity}.json"
    try:
        creds = json.loads(path.read_text())
    except FileNotFoundError:
        raise TokenError(f"no credentials for {identity} in {credentials_dir}; run taktloop issuer init") from None
    except (OSError, ValueError) as e:
        raise TokenError(f"{path}: {e}") from None
    meta = _open(urllib.request.Request(f"{creds['issuer']}/.well-known/oauth-authorization-server"))
    form = urllib.parse.urlencode({
        "grant_type": "client_credentials", "client_id": creds["client_id"], "client_secret": creds["client_secret"],
    }).encode()
    return _open(urllib.request.Request(meta["token_endpoint"], data=form))["access_token"]

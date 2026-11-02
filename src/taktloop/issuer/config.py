"""Loading ``dev-issuer.yaml``."""

import re
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

import yaml


@dataclass(frozen=True)
class IssuerConfig:
    issuer: str
    audience: str
    token_lifetime: timedelta
    state_dir: Path
    identities: dict[str, tuple[str, ...]]  # identity -> scopes

    @property
    def host(self) -> str:
        return urlsplit(self.issuer).hostname or "127.0.0.1"

    @property
    def port(self) -> int:
        return urlsplit(self.issuer).port or 8766


def _lifetime(text: str) -> timedelta:
    m = re.fullmatch(r"(\d+)([hm])", str(text))
    if not m:
        raise ValueError(f"token_lifetime must look like '12h' or '30m', got {text!r}")
    n = int(m[1])
    return timedelta(hours=n) if m[2] == "h" else timedelta(minutes=n)


def load_issuer_config(path: Path) -> IssuerConfig:
    raw = yaml.safe_load(path.read_text())
    identities = {name: tuple(spec["scopes"]) for name, spec in raw["identities"].items()}
    return IssuerConfig(
        issuer=raw["issuer"].rstrip("/"),
        audience=raw["audience"],
        token_lifetime=_lifetime(raw.get("token_lifetime", "12h")),
        state_dir=path.parent / raw.get("state_dir", ".taktloop/issuer"),
        identities=identities,
    )

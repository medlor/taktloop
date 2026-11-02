"""Signing key, secret hashes and client credential files."""

import hashlib
import json
import os
import secrets
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import ec

from taktloop.issuer.config import IssuerConfig


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def load_secret_hashes(state_dir: Path) -> dict[str, str]:
    path = state_dir / "secrets.json"
    return json.loads(path.read_text()) if path.exists() else {}


def load_signing_key(state_dir: Path) -> ec.EllipticCurvePrivateKey:
    return serialization.load_pem_private_key((state_dir / "signing_key.pem").read_bytes(), password=None)


def _write_private(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(text)
    path.chmod(0o600)


def init_state(cfg: IssuerConfig, credentials_dir: Path, rotate: str | None = None) -> list[str]:
    """Create what is missing; return the identities whose credentials were (re)written."""
    if rotate is not None and rotate not in cfg.identities:
        raise ValueError(f"unknown identity {rotate!r}")
    key_path = cfg.state_dir / "signing_key.pem"
    if not key_path.exists():
        pem = ec.generate_private_key(ec.SECP256R1()).private_bytes(
            serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
        )
        _write_private(key_path, pem.decode())
    hashes = load_secret_hashes(cfg.state_dir)
    written = []
    for identity in cfg.identities:
        if identity in hashes and identity != rotate:
            continue
        secret = secrets.token_urlsafe(32)
        hashes[identity] = hash_secret(secret)
        creds = {"issuer": cfg.issuer, "client_id": identity, "client_secret": secret}
        _write_private(credentials_dir / f"{identity}.json", json.dumps(creds, indent=2) + "\n")
        written.append(identity)
    _write_private(cfg.state_dir / "secrets.json", json.dumps(hashes, indent=2) + "\n")
    return written

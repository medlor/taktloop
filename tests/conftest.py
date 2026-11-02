import socket
import threading
import time

import pytest
import uvicorn

from taktloop.issuer.app import build_app
from taktloop.issuer.config import load_issuer_config
from taktloop.issuer.state import init_state


@pytest.fixture(autouse=True)
def _bearer_token(monkeypatch):
    """CLI client calls need a token; any string will do until the core checks it."""
    monkeypatch.setenv("TAKTLOOP_TOKEN", "test-token")


@pytest.fixture
def real_issuer(tmp_path):
    """A dev issuer on a free port (uvicorn in a thread). Yields (issuer URL, credentials dir)."""
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    url = f"http://127.0.0.1:{port}"
    (tmp_path / "dev-issuer.yaml").write_text(
        f"issuer: {url}\naudience: taktloop\ntoken_lifetime: 12h\nstate_dir: state\n"
        "identities:\n  agent-a: {scopes: [taktloop:taktloop:planner, taktloop:taktloop:reviewer]}\n"
        "  tobias: {scopes: [taktloop:taktloop:approver]}\n"
    )
    cfg = load_issuer_config(tmp_path / "dev-issuer.yaml")
    init_state(cfg, tmp_path / "creds")
    server = uvicorn.Server(uvicorn.Config(build_app(cfg), host="127.0.0.1", port=port, log_level="warning"))
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.02)
    yield url, tmp_path / "creds"
    server.should_exit = True
    thread.join(timeout=5)

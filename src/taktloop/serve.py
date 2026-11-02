"""Run the taktloop core: one process, one SQLite file, MCP over streamable HTTP."""

import asyncio
import logging
import signal
import time
from datetime import timedelta
from types import FrameType

import uvicorn

from taktloop.auth import JwtVerifier
from taktloop.config import FactoryConfig
from taktloop.mcp_server import build_server
from taktloop.service import Service
from taktloop.store import Store


class _Server(uvicorn.Server):
    """`uv run` forwards the terminal's Ctrl-C, so one keypress arrives twice;
    uvicorn would read the second as "force quit" and skip the app's shutdown."""

    _last_sigint = float("-inf")

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        if sig == signal.SIGINT:
            now = time.monotonic()
            duplicate = self.should_exit and now - self._last_sigint < 0.5
            self._last_sigint = now
            if duplicate:
                return
        super().handle_exit(sig, frame)


def _cancelled(record: logging.LogRecord) -> bool:
    return bool(record.exc_info) and isinstance(record.exc_info[1], asyncio.CancelledError)


def run(cfg: FactoryConfig) -> None:
    cfg.database.parent.mkdir(parents=True, exist_ok=True)
    service = Service(Store(cfg.database), cfg.workflow, timedelta(minutes=cfg.lease_minutes))
    verifier = JwtVerifier(cfg.auth_issuer, cfg.auth_audience)
    app = build_server(service, cfg.factory, verifier, cfg.url).streamable_http_app(host=cfg.host)
    # Not MCPServer.run(): it sets no graceful-shutdown timeout, and on Python >= 3.12
    # shutdown then waits forever for a client that keeps a request open (Claude
    # Code does). After the timeout, uvicorn cancels those requests.
    server = _Server(uvicorn.Config(app, host=cfg.host, port=cfg.port,
                                    timeout_graceful_shutdown=3))
    # The cancelled requests are expected on shutdown, not errors worth a traceback.
    logging.getLogger("uvicorn.error").addFilter(lambda r: not _cancelled(r))
    print(f"taktloop core for factory '{cfg.factory}' on {cfg.url}", flush=True)
    try:
        server.run()
    except KeyboardInterrupt:  # uvicorn re-raises the Ctrl-C after its clean shutdown
        pass

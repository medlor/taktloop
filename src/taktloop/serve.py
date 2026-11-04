"""Run the taktloop core: one process, one SQLite file, MCP over streamable HTTP."""

import asyncio
import logging
import os
import signal
import time
from datetime import timedelta
from types import FrameType

import uvicorn

from taktloop.auth import JwtVerifier
from taktloop.availability import Availability, needed_tools
from taktloop.config import FactoryConfig
from taktloop.external import Checker, credential, make_checker, make_tool_lister, read_dotenv
from taktloop.http_api import routes
from taktloop.mcp_server import build_server, operations
from taktloop.service import Service
from taktloop.store import Store
from taktloop.workflow import WorkflowError
from taktloop.workflow_file import data_sha, read as read_workflow, write as write_workflow


class _Server(uvicorn.Server):
    """`uv run` forwards Ctrl-C, so one keypress arrives twice; uvicorn would read the second
    as "force quit" and skip the app's shutdown."""

    _last_sigint = float("-inf")

    def handle_exit(self, sig: int, frame: FrameType | None) -> None:
        if sig == signal.SIGINT:
            now = time.monotonic()
            duplicate = self.should_exit and now - self._last_sigint < 0.5
            self._last_sigint = now
            if duplicate:
                return
        super().handle_exit(sig, frame)


def _log_unauthenticated(app):
    """ASGI wrapper: one WARNING per 401 with the reason and the client address."""
    log = logging.getLogger("taktloop.auth")

    async def wrapped(scope, receive, send):
        async def watch(message):
            if message["type"] == "http.response.start" and message["status"] == 401:
                bearer = any(k == b"authorization" for k, _ in scope.get("headers", []))
                host, port = scope.get("client") or ("?", 0)
                log.warning("401 for %s:%s %s: %s", host, port, scope.get("path"),
                            "bearer token refused" if bearer else "no bearer token")
            await send(message)

        await app(scope, receive, watch if scope["type"] == "http" else send)

    return wrapped


def build_checker(cfg: FactoryConfig) -> Checker:
    dotenv = read_dotenv(cfg.env_file)
    for server in cfg.mcp_servers.values():
        if credential(server, os.environ, dotenv) is None:
            logging.getLogger("taktloop").warning(
                "no credential for %s: set %s in the environment or in .env; "
                "checks against it will be refused", server.name, server.token_env)
    return make_checker(cfg.mcp_servers, os.environ, dotenv)


def prepare(cfg: FactoryConfig) -> Service:
    """Build the service from workflow.yaml; rewrite the file if a hand edit became a new version."""
    workflow = read_workflow(cfg.workflow_path)
    cfg.database.parent.mkdir(parents=True, exist_ok=True)
    store = Store(cfg.database)
    avail = Availability(make_tool_lister(cfg.mcp_servers, os.environ, read_dotenv(cfg.env_file)),
                         lambda: needed_tools(service.live, ("check",), cfg.sync))
    tools_check = lambda wf: avail.check(needed_tools(wf, ("check", "call")))
    with store.transaction() as tx:
        latest = tx.latest_workflow_version()
    if latest and latest.sha256 != data_sha(workflow.data) and (refusal := tools_check(workflow)):
        raise WorkflowError(f"{cfg.workflow_path.name}: {refusal}")  # a hand edit that cannot run
    service = Service(store, workflow, timedelta(minutes=cfg.lease_minutes),
                      separation=cfg.separation, checker=build_checker(cfg),
                      servers=cfg.mcp_servers.keys(), workflow_path=cfg.workflow_path,
                      tools_check=tools_check, availability=avail, sync=cfg.sync)
    for server, problem in avail.problems().items():
        logging.getLogger("taktloop").warning("%s %s", server, problem)
    if service.live_version != workflow.version:
        write_workflow(cfg.workflow_path, service.live.data, service.live_version)
    return service


def run(cfg: FactoryConfig) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    logging.getLogger("taktloop").addHandler(handler)
    service = prepare(cfg)
    verifier = JwtVerifier(cfg.auth_issuer, cfg.auth_audience)
    app = build_server(service, cfg.factory, verifier, cfg.url).streamable_http_app(host=cfg.host)
    app.routes.extend(routes(operations(service, cfg.factory), cfg.factory, verifier))
    print(f"taktloop core for factory '{cfg.factory}' on {cfg.url}", flush=True)
    serve_app(app, cfg.host, cfg.port)


def serve_app(app, host: str, port: int) -> None:
    """Run an MCP ASGI app until Ctrl-C: 401s logged, graceful shutdown bounded."""
    app = _log_unauthenticated(app)
    # Not MCPServer.run(): it sets no graceful-shutdown timeout, and on Python >= 3.12
    # shutdown then waits forever for a client that keeps a request open (Claude
    # Code does). After the timeout, uvicorn cancels those requests.
    server = _Server(uvicorn.Config(app, host=host, port=port, timeout_graceful_shutdown=3))
    # The cancelled requests are expected on shutdown, not errors worth a traceback.
    logging.getLogger("uvicorn.error").addFilter(
        lambda r: not (r.exc_info and isinstance(r.exc_info[1], asyncio.CancelledError)))
    try:
        server.run()
    except KeyboardInterrupt:  # uvicorn re-raises the Ctrl-C after its clean shutdown
        pass

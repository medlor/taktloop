"""Read factory.yaml into a FactoryConfig."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

from taktloop.external import ExternalServer
from taktloop.workflow import Workflow, WorkflowError, parse_workflow


@dataclass(frozen=True)
class FactoryConfig:
    factory: str
    host: str
    port: int
    database: Path
    workflow: Workflow
    lease_minutes: int
    auth_issuer: str
    auth_audience: str
    cli_identity: str | None = None
    separation: Literal["off", "strict"] = "off"
    credentials: Path = Path(".taktloop/credentials")  # next to factory.yaml, like the database
    mcp_servers: Mapping[str, ExternalServer] = field(default_factory=dict)
    env_file: Path = Path(".env")

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/mcp"


def load_config(path: Path) -> FactoryConfig:
    path = Path(path)
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict) or "workflow" not in data:
        raise WorkflowError(f"{path}: missing 'workflow' block")
    server = data.get("server") or {}
    auth = data.get("auth")
    if not isinstance(auth, dict) or not auth.get("issuer") or not auth.get("audience"):
        raise WorkflowError(f"{path.name}: 'auth' block with issuer and audience is required")
    separation = data.get("separation", "off")
    separation = "off" if separation is False else separation  # YAML reads a bare off as False
    if separation not in ("off", "strict"):
        raise WorkflowError(f"{path.name}: separation must be 'off' or 'strict'")
    servers = {}
    for name, raw in (data.get("mcp_servers") or {}).items():
        if not isinstance(raw, dict) or not raw.get("url") or not raw.get("token_env"):
            raise WorkflowError(f"{path.name}: mcp_servers.{name} needs url and token_env")
        servers[str(name)] = ExternalServer(str(name), str(raw["url"]), str(raw["token_env"]))
    workflow = parse_workflow(data["workflow"])
    for step_name, step in workflow.steps.items():
        if step.check and step.check.mcp_server not in servers:
            raise WorkflowError(f"step {step_name!r}: check uses unknown mcp_server {step.check.mcp_server!r}")
    return FactoryConfig(
        factory=str(data.get("factory") or path.resolve().parent.name),
        host=str(server.get("host", "127.0.0.1")),
        port=int(server.get("port", 8765)),
        database=path.parent / data.get("database", ".taktloop/factory.db"),
        workflow=workflow,
        lease_minutes=int(data.get("lease_minutes", 30)),
        auth_issuer=str(auth["issuer"]),
        auth_audience=str(auth["audience"]),
        cli_identity=(data.get("cli") or {}).get("identity"),
        separation=separation,
        credentials=path.parent / ".taktloop/credentials",
        mcp_servers=servers,
        env_file=path.parent / ".env",
    )

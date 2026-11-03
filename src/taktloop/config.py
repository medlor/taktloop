"""Read factory.yaml into a FactoryConfig."""

from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import yaml

from taktloop import workflow_file
from taktloop.checks import CheckDecl, parse_check
from taktloop.external import ExternalServer
from taktloop.workflow import Workflow, WorkflowError


@dataclass(frozen=True)
class OrchestratorConfig:
    identity: str
    tick_seconds: int = 15
    code_retry_seconds: int = 60
    stall_after_minutes: int = 60
    max_lease_expiries: int = 2


@dataclass(frozen=True)
class SyncConfig:
    check: CheckDecl
    every_minutes: int = 5


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
    orchestrator: OrchestratorConfig | None = None
    root: Path = Path(".")  # the directory of factory.yaml
    workflow_path: Path = Path("workflow.yaml")
    product_checkout: Path = Path(".")
    hooks_path: Path = Path("hooks.yaml")
    sync: Mapping[str, SyncConfig] = field(default_factory=dict)

    @property
    def url(self) -> str:
        return f"http://{self.host}:{self.port}/mcp"


def load_config(path: Path) -> FactoryConfig:
    path = Path(path)
    data = yaml.safe_load(path.read_text()) or {}
    if not isinstance(data, dict):
        raise WorkflowError(f"{path}: expected a mapping")
    if "workflow" in data:
        raise WorkflowError("factory.yaml: move the workflow block to workflow.yaml (see docs/user/workflow-reference.md)")
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
    workflow_path = path.parent / data.get("workflow_file", "workflow.yaml")
    workflow = workflow_file.read(workflow_path)
    for step_name, step in workflow.steps.items():
        for what, decl in (("check", step.check), ("call", step.call)):
            if decl and decl.mcp_server not in servers:
                raise WorkflowError(f"step {step_name!r}: {what} uses unknown mcp_server {decl.mcp_server!r}")
    sync = {}
    raw_sync = data.get("sync") or {}
    if not isinstance(raw_sync, dict):
        raise WorkflowError(f"{path.name}: sync must be a mapping")
    for name, raw in raw_sync.items():
        try:
            if not isinstance(raw, dict):
                raise ValueError("must be a mapping")
            every = raw.get("every_minutes", 5)
            if type(every) is not int or every < 1:
                raise ValueError("every_minutes must be a positive integer")
            check = parse_check({k: v for k, v in raw.items() if k != "every_minutes"})
        except ValueError as e:
            raise WorkflowError(f"{path.name}: sync.{name}: {e}") from e
        if check.mcp_server not in servers:
            raise WorkflowError(f"{path.name}: sync.{name}: unknown mcp_server {check.mcp_server!r}")
        sync[str(name)] = SyncConfig(check, every)
    orch = data.get("orchestrator")
    if orch is not None:
        if not isinstance(orch, dict) or not orch.get("identity"):
            raise WorkflowError(f"{path.name}: orchestrator needs an identity")
        keys = ("tick_seconds", "code_retry_seconds", "stall_after_minutes", "max_lease_expiries")
        orch = OrchestratorConfig(str(orch["identity"]), **{k: int(orch[k]) for k in keys if k in orch})
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
        orchestrator=orch,
        root=path.parent,
        workflow_path=workflow_path,
        product_checkout=path.parent / (data.get("product") or {}).get("checkout", "."),
        hooks_path=path.parent / "hooks.yaml",
        sync=sync,
    )

"""Read factory.yaml into a FactoryConfig."""

from dataclasses import dataclass
from pathlib import Path

import yaml

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
    return FactoryConfig(
        factory=str(data.get("factory") or path.resolve().parent.name),
        host=str(server.get("host", "127.0.0.1")),
        port=int(server.get("port", 8765)),
        database=path.parent / data.get("database", ".taktloop/factory.db"),
        workflow=parse_workflow(data["workflow"]),
        lease_minutes=int(data.get("lease_minutes", 30)),
        auth_issuer=str(auth["issuer"]),
        auth_audience=str(auth["audience"]),
        cli_identity=(data.get("cli") or {}).get("identity"),
    )

"""Read and write workflow.yaml: ``version`` plus the ``start``/``steps`` mapping."""

import hashlib
import json
import os
from collections.abc import Mapping
from pathlib import Path

import yaml

from taktloop.workflow import Workflow, WorkflowError, parse_workflow


def read(path: Path) -> Workflow:
    try:
        data = yaml.safe_load(Path(path).read_text())
    except (yaml.YAMLError, OSError) as e:
        raise WorkflowError(f"{path}: {' '.join(str(e).split())}") from e
    if not isinstance(data, dict):
        raise WorkflowError(f"{path}: expected a mapping with start and steps")
    try:  # only what JSON can hold goes on: snapshots and the sha are JSON
        data = json.loads(json.dumps(data))
    except (TypeError, ValueError) as e:
        raise WorkflowError(f"{path}: {' '.join(str(e).split())}") from e
    version = data.pop("version", 1)
    if type(version) is not int:
        raise WorkflowError(f"{path}: version must be an integer")
    try:
        return parse_workflow(data, version)
    except WorkflowError as e:
        raise WorkflowError(f"{path}: {e}") from e


def write(path: Path, data: Mapping, version: int) -> None:
    path = Path(path)
    tmp = path.with_name(f".{path.name}.tmp")
    tmp.write_text(yaml.safe_dump({"version": version, **data}, sort_keys=False))
    os.replace(tmp, path)


def data_sha(data: Mapping) -> str:
    return hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()

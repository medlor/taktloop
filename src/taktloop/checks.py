"""Gate check declarations (factory.yaml ``check:``). Pure, no I/O."""

import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

PLACEHOLDER = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
FIELDS = ("mcp_server", "tool", "args", "match", "expect")


@dataclass(frozen=True)
class CheckDecl:
    mcp_server: str
    tool: str
    args: Mapping[str, str]
    match: Mapping[str, str]
    expect: Mapping[str, str]


def _strings(raw: Any, field: str) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"check {field} must be a mapping")
    return {str(k): str(v) for k, v in raw.items()}


def parse_check(raw: Any) -> CheckDecl:
    if not isinstance(raw, dict):
        raise ValueError("check must be a mapping")
    unknown = sorted(set(raw) - set(FIELDS))
    if unknown:
        raise ValueError(f"unknown check field {unknown[0]!r}")
    for field in ("mcp_server", "tool"):
        if not isinstance(raw.get(field), str) or not raw[field]:
            raise ValueError(f"check needs {field} (a string)")
    expect = _strings(raw.get("expect"), "expect")
    if not expect:
        raise ValueError("check needs a non-empty expect mapping")
    return CheckDecl(raw["mcp_server"], raw["tool"], _strings(raw.get("args"), "args"),
                     _strings(raw.get("match"), "match"), expect)


def placeholders(decl: CheckDecl) -> set[str]:
    values = [*decl.args.values(), *decl.match.values(), *decl.expect.values()]
    return {m for v in values for m in PLACEHOLDER.findall(v)}

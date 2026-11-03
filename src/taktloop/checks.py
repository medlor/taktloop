"""Gate check declarations (factory.yaml ``check:``). Pure, no I/O."""

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from typing import Any

from taktloop.models import Artifact

PLACEHOLDER = re.compile(r"\{\{\s*([^{}]+?)\s*\}\}")
FIELDS = ("mcp_server", "tool", "args", "match", "expect")


@dataclass(frozen=True)
class CheckDecl:
    mcp_server: str
    tool: str
    args: Mapping[str, Any]
    match: Mapping[str, str]
    expect: Mapping[str, str]


def _strings(raw: Any, field: str) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ValueError(f"check {field} must be a mapping")
    return {str(k): str(v) for k, v in raw.items()}


@dataclass(frozen=True)
class CallDecl:
    mcp_server: str
    tool: str
    args: Mapping[str, Any]
    evidence: Mapping[str, str]  # evidence key -> dotted path into the result


def _args(raw: Any, what: str) -> dict[str, Any]:
    if raw is not None and not isinstance(raw, dict):
        raise ValueError(f"{what} args must be a mapping")
    return dict(raw or {})


def _server_tool(raw: Any, what: str) -> None:
    if not isinstance(raw, dict):
        raise ValueError(f"{what} must be a mapping")
    for field in ("mcp_server", "tool"):
        if not isinstance(raw.get(field), str) or not raw[field]:
            raise ValueError(f"{what} needs {field} (a string)")


def parse_call(raw: Any) -> CallDecl:
    _server_tool(raw, "call")
    unknown = sorted(set(raw) - {"mcp_server", "tool", "args", "evidence"})
    if unknown:
        raise ValueError(f"unknown call field {unknown[0]!r}")
    evidence = raw.get("evidence")
    if (not isinstance(evidence, dict) or not evidence
            or not all(isinstance(k, str) and isinstance(v, str) for k, v in evidence.items())):
        raise ValueError("call needs a non-empty evidence mapping of strings")
    return CallDecl(raw["mcp_server"], raw["tool"], _args(raw.get("args"), "call"), dict(evidence))


def parse_check(raw: Any) -> CheckDecl:
    _server_tool(raw, "check")
    unknown = sorted(set(raw) - set(FIELDS))
    if unknown:
        raise ValueError(f"unknown check field {unknown[0]!r}")
    expect = _strings(raw.get("expect"), "expect")
    if not expect:
        raise ValueError("check needs a non-empty expect mapping")
    return CheckDecl(raw["mcp_server"], raw["tool"], _args(raw.get("args"), "check"),
                     _strings(raw.get("match"), "match"), expect)


def _strs(node: Any) -> list[str]:
    if isinstance(node, str):
        return [node]
    if isinstance(node, Mapping):
        node = list(node.values())
    return [s for v in node for s in _strs(v)] if isinstance(node, list) else []


def placeholders(decl: CheckDecl | CallDecl) -> set[str]:
    parts = [getattr(decl, f) for f in ("args", "match", "expect") if hasattr(decl, f)]
    return {m for s in _strs(parts) for m in PLACEHOLDER.findall(s)}


class CheckUnavailable(Exception):
    """The check could not be evaluated; str(e) is the reason."""


class ToolFailed(CheckUnavailable):
    """The tool itself answered with an error."""


@dataclass(frozen=True)
class Mismatch:
    field: str
    got: str | None
    want: str


def substitute(value: Any, evidence: Mapping[str, str], artifacts: Mapping[str, Artifact],
               values: Mapping[str, str] = {}) -> Any:
    """``value`` with every placeholder in any string, at any depth, replaced."""
    def lookup_key(key: str) -> str:
        if key in values:
            return str(values[key])
        kind, _, rest = key.partition(".")
        name, _, attr = rest.partition(".")
        if kind == "evidence" and rest in evidence:
            return str(evidence[rest])
        if kind == "artifacts" and name in artifacts and attr in ("commit", "branch", "path"):
            return getattr(artifacts[name], attr)
        raise CheckUnavailable(f"cannot resolve {{{{{key}}}}}")

    if isinstance(value, str):
        return PLACEHOLDER.sub(lambda m: lookup_key(m.group(1)), value)
    if isinstance(value, Mapping):
        return {k: substitute(v, evidence, artifacts, values) for k, v in value.items()}
    if isinstance(value, list):
        return [substitute(v, evidence, artifacts, values) for v in value]
    return value


def resolve(decl: CheckDecl, evidence: Mapping[str, str], artifacts: Mapping[str, Artifact],
            values: Mapping[str, str] = {}) -> CheckDecl:
    return replace(decl, **{f: substitute(getattr(decl, f), evidence, artifacts, values)
                            for f in ("args", "match", "expect")})


def read_result(structured: dict | None, texts: list[str]) -> dict:
    if isinstance(structured, dict):
        return structured
    try:
        parsed = json.loads(texts[0])
    except (IndexError, ValueError):
        parsed = None
    if not isinstance(parsed, dict):
        raise CheckUnavailable("result is not a JSON object")
    return parsed


def lookup(result: Mapping, path: str) -> str | None:
    """The value at a dotted path as a string, or None when missing."""
    node: Any = result
    for part in path.split("."):
        if isinstance(node, list):
            node = node[int(part)] if part.isdigit() and int(part) < len(node) else None
        else:
            node = node.get(part) if isinstance(node, Mapping) else None
    return None if node is None else str(node)


def compare(fields: Mapping[str, str], result: Mapping) -> list[Mismatch]:
    return [Mismatch(p, got, want) for p, want in fields.items()
            if (got := lookup(result, p)) != want]


def describe(m: Mismatch) -> str:
    return f"{m.field} is {'missing' if m.got is None else m.got}, expected {m.want}"

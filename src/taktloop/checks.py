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


class CheckUnavailable(Exception):
    """The check could not be evaluated; str(e) is the reason."""


@dataclass(frozen=True)
class Mismatch:
    field: str
    got: str | None
    want: str


def resolve(decl: CheckDecl, evidence: Mapping[str, str],
            artifacts: Mapping[str, Artifact]) -> CheckDecl:
    def value(key: str) -> str:
        kind, _, rest = key.partition(".")
        name, _, attr = rest.partition(".")
        if kind == "evidence" and rest in evidence:
            return str(evidence[rest])
        if kind == "artifacts" and name in artifacts and attr in ("commit", "branch", "path"):
            return getattr(artifacts[name], attr)
        raise CheckUnavailable(f"cannot resolve {{{{{key}}}}}")

    def sub(values: Mapping[str, str]) -> dict[str, str]:
        return {k: PLACEHOLDER.sub(lambda m: value(m.group(1)), v) for k, v in values.items()}

    return replace(decl, args=sub(decl.args), match=sub(decl.match), expect=sub(decl.expect))


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
        node = node.get(part) if isinstance(node, Mapping) else None
    return None if node is None else str(node)


def compare(fields: Mapping[str, str], result: Mapping) -> list[Mismatch]:
    return [Mismatch(p, got, want) for p, want in fields.items()
            if (got := lookup(result, p)) != want]


def describe(m: Mismatch) -> str:
    return f"{m.field} is {'missing' if m.got is None else m.got}, expected {m.want}"

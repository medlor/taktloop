"""Import boundary between the core and the dev issuer, and the source line budget."""

import ast
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src" / "taktloop"
ISSUER = SRC / "issuer"
# The CLI starts the issuer (`taktloop issuer`); argparse lives in cli.py, the handler in cli_commands.py.
STARTS_ISSUER = {SRC / "cli.py", SRC / "cli_commands.py"}


def _modules() -> list[Path]:
    return sorted(SRC.rglob("*.py"))


def _imports(path: Path) -> set[str]:
    names = set()
    for node in ast.walk(ast.parse(path.read_text(), str(path))):
        if isinstance(node, ast.Import):
            names.update(a.name for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
            names.update(f"{node.module}.{a.name}" for a in node.names)
        elif isinstance(node, ast.ImportFrom):  # relative import: resolve against the package
            base = path.parent
            for _ in range(node.level - 1):
                base = base.parent
            pkg = ".".join(base.relative_to(SRC.parent).parts)
            mod = f"{pkg}.{node.module}" if node.module else pkg
            names.add(mod)
            names.update(f"{mod}.{a.name}" for a in node.names)
    return names


def _is(name: str, package: str) -> bool:
    return name == package or name.startswith(package + ".")


def test_only_the_cli_imports_the_issuer():
    offenders = {str(p.relative_to(SRC)): sorted(n for n in _imports(p) if _is(n, "taktloop.issuer"))
                 for p in _modules() if ISSUER not in p.parents and p not in STARTS_ISSUER}
    assert {k: v for k, v in offenders.items() if v} == {}


def test_issuer_imports_nothing_else_from_taktloop():
    offenders = {str(p.relative_to(SRC)): sorted(n for n in _imports(p)
                                                 if _is(n, "taktloop") and not _is(n, "taktloop.issuer"))
                 for p in ISSUER.rglob("*.py")}
    assert {k: v for k, v in offenders.items() if v} == {}


def test_line_budget():
    """Counted like `wc -l` over src/taktloop/**/*.py: at most 2,900 in total (M3b hard cap), 300 per module."""
    lines = {str(p.relative_to(SRC)): len(p.read_text().splitlines()) for p in _modules()}
    assert {k: n for k, n in lines.items() if n > 300} == {}
    assert sum(lines.values()) <= 2900, sorted(lines.items(), key=lambda kv: -kv[1])

# CLI `--version` Flag Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `taktloop --version` prints the installed CLI's version and exits 0.

**Architecture:** One argparse `action="version"` argument on the top-level parser in `_parser()`. The version string comes from `importlib.metadata.version("taktloop")`, which `cli.py` already imports and uses for the MCP client info, so `pyproject.toml` stays the only source of the number.

**Tech Stack:** Python, argparse, `importlib.metadata`, pytest, uv.

**Spec:** `docs/superpowers/specs/2026-11-01-cli-version-flag-design.md`

## Global Constraints

- Output is exactly `taktloop <version>` on stdout (for example `taktloop 0.1.0`), exit code 0.
- No network call and no config read when `--version` is given.
- No second copy of the version number: no `__version__` constant, no hard-coded `0.1.0` in code or tests.
- The flag is on the top-level parser only. `taktloop task --version` is not supported.
- `--json` does not change the output.
- No special handling for missing package metadata.

## Review Focus

1. `taktloop --version` with no subcommand: the subparsers are `required=True`, so the flag must exit before argparse reports a missing command.
2. `--version` together with `--json`: still the plain line, not JSON.
3. `--version` with a `--config` path that doesn't exist: exits 0, because no config is read.
4. `--version` when no core is running: exits 0, no connection attempt.
5. `--version` after a subcommand (`taktloop task --version`): rejected as an unknown argument (argparse exit 2), not silently accepted.

Each line is pinned by a test in Task 1.

---

### Task 1: Top-level `--version` flag

**Files:**
- Modify: `src/taktloop/cli.py` (`_parser()`, right after `p = argparse.ArgumentParser(prog="taktloop")`, around line 155)
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `version` from `importlib.metadata` (already imported in `cli.py`).
- Produces: nothing other tasks use.

- [ ] **Step 1: Write the failing tests** (append to `tests/test_cli.py`)

```python
from importlib.metadata import version


@pytest.mark.parametrize("argv", [
    ["--version"],
    ["--json", "--version"],
    ["--config", "/nonexistent/factory.yaml", "--version"],
    ["--url", "http://127.0.0.1:1/mcp", "--version"],
])  # fmt: skip
def test_version_flag(argv, capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(argv)
    assert exc.value.code == 0
    out = capsys.readouterr()
    assert out.out == f"taktloop {version('taktloop')}\n"
    assert out.err == ""


def test_version_flag_not_on_subcommand(capsys):
    with pytest.raises(SystemExit) as exc:
        cli.main(["task", "--version"])
    assert exc.value.code == 2
    assert "unrecognized arguments: --version" in capsys.readouterr().err
```

The `--url` case points at a closed port: it would fail with exit 2 if `main` tried to connect, so passing it proves there's no network call.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_cli.py -k version -v`
Expected: the four `test_version_flag` cases FAIL (argparse exits 2 with "unrecognized arguments" or "the following arguments are required"). `test_version_flag_not_on_subcommand` may already pass. That's fine: it guards against the flag being added to `shared`.

- [ ] **Step 3: Add the flag in `_parser()`**

Add to `p` only (not to `common()` or `shared`):
`action="version"`, `version=f"%(prog)s {version('taktloop')}"`.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/test_cli.py -k version -v`
Expected: all 5 PASS.

- [ ] **Step 5: Run the full suite and a manual check**

Run: `uv run pytest -q`
Expected: all pass.

Run: `uv run taktloop --version; echo "exit=$?"`
Expected: `taktloop 0.1.0` then `exit=0`.

- [ ] **Step 6: Commit**

```bash
git add src/taktloop/cli.py tests/test_cli.py
git commit -m "CLI: add --version flag"
```

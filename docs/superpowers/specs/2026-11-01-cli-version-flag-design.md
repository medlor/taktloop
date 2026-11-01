# CLI `--version` flag — design (short)

Task #1 "Add a --version flag to the CLI". Size: small change.

## Intent

Let a user check which taktloop CLI is installed: `taktloop --version`.

## Goals / non-goals

- Goal: `taktloop --version` prints the installed CLI's version and exits 0.
- Non-goal: reporting the running core's version. No network call and no
  config read (requester: "only this cli").
- Anti-goal: a second copy of the version number that can drift from
  `pyproject.toml`.

## Approaches

1. **argparse `action="version"` on the top-level parser, version from
   `importlib.metadata.version("taktloop")`.** Chosen. One line, one source of
   truth (`pyproject.toml`), and it reuses the lookup `cli.py` already does for
   the MCP client info.
2. `__version__` constant in `taktloop/__init__.py`. Rejected: it duplicates
   the number, or else the build has to change to derive it.
3. `taktloop version` subcommand. Rejected: it isn't the conventional flag
   that was asked for.

## Decided

- Add `--version` to the top-level parser in `_parser()` (`src/taktloop/cli.py`)
  with `action="version"`, `version=f"%(prog)s {version('taktloop')}"`.
- Output: `taktloop 0.1.0` on stdout, exit code 0 (argparse default behaviour).
- Test in `tests/test_cli.py`: `main(["--version"])` exits 0 and prints
  `taktloop <importlib.metadata version>`.

## Builder's calls (Claude's proposals)

- The flag is on the top-level parser only. `taktloop task --version` is not
  supported.
- `--json` doesn't affect it. The output is always the plain line.
- If package metadata is missing (not installed), the CLI fails the same way
  it already does at `cli.py:65`. No special handling; `uv run` always
  installs the project.

"""Start a read-only headless Claude Code run for a pushed step."""

import json
import subprocess
from collections.abc import Callable, Iterable, Mapping
from pathlib import Path

READ_ONLY_TOOLS = ("Read", "Grep", "Glob", "Skill", "Bash(git diff:*)", "Bash(git log:*)",
                   "Bash(git show:*)", "Bash(git rev-parse:*)", "mcp__taktloop__*")
DENIED_TOOLS = ("Edit", "Write", "NotebookEdit")


class StartFailed(Exception):
    """The reason (never a secret) a headless run could not start."""


def claude_auth(run: Callable = subprocess.run) -> dict | None:
    try:
        done = run(["claude", "auth", "status"], capture_output=True, text=True, timeout=30)
        return json.loads(done.stdout) if done.returncode == 0 else None
    except (OSError, subprocess.SubprocessError, ValueError):
        return None


def child_env(environ: Mapping[str, str], auth: dict | None, token: str) -> dict[str, str]:
    env = {**environ, "TAKTLOOP_TOKEN": token}
    if auth and auth.get("loggedIn") is True and auth.get("authMethod") == "claude.ai":
        env.pop("ANTHROPIC_API_KEY", None)
    elif not env.get("ANTHROPIC_API_KEY"):
        raise StartFailed("no Claude login and no ANTHROPIC_API_KEY")
    return env


def command(step_id: int) -> list[str]:
    return ["claude", "-p", f"work on taktloop step {step_id}", "--permission-mode", "dontAsk",
            "--allowedTools", *READ_ONLY_TOOLS, "--disallowedTools", *DENIED_TOOLS]


def ensure_commits(root: Path, shas: Iterable[str], run: Callable = subprocess.run) -> None:
    for sha in shas:
        if run(["git", "cat-file", "-e", f"{sha}^{{commit}}"], cwd=root, capture_output=True).returncode:
            run(["git", "fetch", "origin"], cwd=root, capture_output=True)  # failure ignored
            return


def start(step_id: int, token: str, root: Path, environ: Mapping[str, str],
          shas: Iterable[str]) -> tuple[subprocess.Popen, Path]:
    ensure_commits(root, shas)
    env = child_env(environ, claude_auth(), token)
    runs = root / ".taktloop" / "runs"
    runs.mkdir(parents=True, exist_ok=True)
    n = 1
    while (log := runs / f"{step_id}-{n}.log").exists():
        n += 1
    with log.open("wb") as out:
        try:
            proc = subprocess.Popen(command(step_id), cwd=root, env=env, stdout=out,
                                    stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
        except FileNotFoundError:
            raise StartFailed("claude is not on PATH") from None
    return proc, log

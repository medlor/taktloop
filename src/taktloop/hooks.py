"""Hooks (hooks.yaml): an MCP call or a command for each matching core event, in order, at least once."""

import json
import shlex
import subprocess
from collections.abc import Callable, Collection, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from taktloop.checks import CheckUnavailable, ToolFailed, _args, _server_tool, substitute
from taktloop.external import Checker

class HookFailed(Exception):
    """A command hook exited non-zero or timed out."""


@dataclass(frozen=True)
class Hook:
    name: str
    on: str
    step: str | None = None
    map: Mapping[str, str] | None = None
    call: tuple[str, str, Mapping[str, Any]] | None = None  # server, tool, args
    command: str | None = None


def parse_hooks(data: Any, servers: Collection[str]) -> list[Hook]:
    items = (data or {}).get("hooks", []) if isinstance(data or {}, dict) else None
    if not isinstance(items, list):
        raise ValueError("expected a 'hooks' list")
    out: list[Hook] = []
    for raw in items:
        if isinstance(raw, dict):  # YAML 1.1 reads a bare `on` key as true
            raw = {"on" if k is True else k: v for k, v in raw.items()}
        if not isinstance(raw, dict) or not raw.get("name") or not raw.get("on"):
            raise ValueError("every hook needs name and on")
        name, call, mapping = str(raw["name"]), raw.get("call"), raw.get("map")
        if unknown := sorted(set(raw) - {"name", "on", "step", "map", "call", "command"}):
            raise ValueError(f"hook {name}: unknown key {unknown[0]!r}")
        if name in {h.name for h in out}:
            raise ValueError(f"duplicate hook name {name!r}")
        if ("call" in raw) == ("command" in raw):
            raise ValueError(f"hook {name}: needs exactly one of call or command")
        if "command" in raw and not (isinstance(raw["command"], str) and shlex.split(raw["command"])):
            raise ValueError(f"hook {name}: command must be a non-empty command line")
        if "call" in raw:
            _server_tool(call, f"hook {name}: call", ("mcp_server", "tool", "args"))
            if call["mcp_server"] not in servers:
                raise ValueError(f"hook {name}: unknown mcp_server {call['mcp_server']!r}")
            call = (call["mcp_server"], call["tool"], _args(call.get("args"), "call"))
        if mapping is not None and not isinstance(mapping, dict) or not isinstance(raw.get("step", ""), str):
            raise ValueError(f"hook {name}: map must be a mapping, step a string")
        out.append(Hook(name, str(raw["on"]), raw.get("step"), mapping and {str(k): str(v) for k, v in mapping.items()},
                        call, raw.get("command")))
    return out


def run_command(command: str, payload: dict, cwd: Path, environ: Mapping[str, str],
                secret_envs: Collection[str], timeout: float = 30.0) -> None:
    env = {k: v for k, v in environ.items() if not k.startswith("TAKTLOOP_") and k not in secret_envs}
    try:
        done = subprocess.run(shlex.split(command), input=json.dumps(payload), text=True, cwd=cwd, env=env,
                              capture_output=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        raise HookFailed(f"timed out after {timeout:g} s") from None
    except (OSError, ValueError, IndexError) as e:  # IndexError: an empty command line
        raise HookFailed(f"cannot run {command!r}: {type(e).__name__}") from None
    if done.returncode:
        raise HookFailed(f"exit {done.returncode}")


class HookRunner:
    def __init__(self, path: Path, cursor_path: Path, servers: Collection[str], call: Checker,
                 run_command: Callable[[str, dict], None], log: Callable[[str], None], max_attempts: int = 3) -> None:
        self.path, self.cursor_path, self.servers, self.call = path, cursor_path, servers, call
        self.run_command, self.log, self.max_attempts = run_command, log, max_attempts
        self.hooks: list[Hook] = []
        self._mtime, self._failures = None, {}  # failures: (hook, event id) -> failed attempts

    def reload(self) -> None:
        mtime = self.path.stat().st_mtime_ns if self.path.exists() else None
        if mtime == self._mtime:
            return
        self._mtime = mtime
        try:
            self.hooks = parse_hooks(mtime and yaml.safe_load(self.path.read_text()), self.servers)
        except (ValueError, yaml.YAMLError) as e:
            self.log(f"hooks.yaml refused: {' '.join(str(e).split())}; keeping the previous hooks")

    def tick(self, list_events: Callable[[int], dict], unavailable: Callable[[str], bool]) -> None:
        if not self.hooks:
            return
        cursors = json.loads(self.cursor_path.read_text()) if self.cursor_path.exists() else {}
        events, after = [], min((cursors[h.name] for h in self.hooks if h.name in cursors), default=0)
        while page := list_events(after)["events"]:  # a new hook needs the newest id, so read to the end
            events, after = events + page, page[-1]["id"]
        for h in self.hooks:
            cursors.setdefault(h.name, after)
            for ev in (e for e in events if e["id"] > cursors[h.name]):
                if not self._fire(h, ev, unavailable):
                    break
                cursors[h.name] = ev["id"]
                self._save(cursors)
        self._save(cursors)

    def _save(self, cursors: dict[str, int]) -> None:
        self.cursor_path.parent.mkdir(parents=True, exist_ok=True)
        self.cursor_path.write_text(json.dumps(cursors))

    def _fire(self, h: Hook, ev: dict, unavailable: Callable[[str], bool]) -> bool:
        """True: done with this event (handled, skipped or given up); False: try it again next tick."""
        if ev["operation"] != h.on or h.step and ev["step"] != h.step:
            return True
        event = {**ev["detail"], "step": ev["step"]}
        values = {**ev["values"], **{f"event.{k}": str(v) for k, v in event.items() if v is not None}}
        try:
            if h.map is not None:
                if ev["step"] not in h.map:
                    raise CheckUnavailable(f"step {ev['step']!r} is not in its map")
                values["mapped"] = h.map[ev["step"]]
            args = substitute(h.call[2], {}, {}, values) if h.call else {}
        except CheckUnavailable as e:
            self.log(f"hook {h.name} skipped event {ev['id']}: {e}")
            return True
        if h.call and unavailable(h.call[0]):
            return False
        try:
            self.call(h.call[0], h.call[1], args) if h.call else self.run_command(h.command, ev)
            return True
        except (ToolFailed, HookFailed) as e:
            reason = str(e)
        except CheckUnavailable:
            return False  # unreachable: wait, no attempt counted
        key = (h.name, ev["id"])
        if (n := self._failures.get(key, 0) + 1) < self.max_attempts:
            self._failures[key] = n
            return False
        self._failures.pop(key, None)
        line = f"hook {h.name} gave up on event {ev['id']}: {reason}"
        self.log(line)
        with (self.cursor_path.parent / "hooks.log").open("a") as f:
            f.write(line + "\n")
        return True

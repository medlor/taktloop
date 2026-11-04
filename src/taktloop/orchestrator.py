"""The orchestrator: a core client that runs code steps, starts pushed steps and reports stalls."""

import os
import time
from collections import Counter
from collections.abc import Callable
from contextlib import suppress
from datetime import datetime, timedelta, timezone
from functools import partial
from pathlib import Path

import anyio

from taktloop import headless
from taktloop.availability import Availability, describe, needed_tools
from taktloop.checks import lookup, substitute
from taktloop.cli_commands import TokenRejected, ToolRejected, _call_tool
from taktloop.config import FactoryConfig
from taktloop.external import Checker, make_checker, make_tool_lister, read_dotenv
from taktloop.hooks import HookRunner, run_command
from taktloop.models import Artifact
from taktloop.token_client import TokenError, fetch_token
from taktloop.workflow import Workflow, parse_workflow

LOST = ("not claimed", "claimed by", "claim token")  # the claim is gone: never retry with it

utc_now = partial(datetime.now, timezone.utc)


class CoreClient:
    """Calls core tools as one identity; a 401 fetches a new token once and retries."""

    def __init__(self, url: str, identity: str, credentials: Path) -> None:
        self._url, self._fetch, self._token = url, partial(fetch_token, identity, credentials), None

    def call(self, tool: str, args: dict) -> dict:
        for retry in (False, True):
            self._token = self._fetch() if retry or not self._token else self._token
            try:
                return anyio.run(_call_tool, self._url, tool, args, self._token, "taktloop-orchestrator")
            except TokenRejected:
                if retry:
                    raise


class Orchestrator:
    def __init__(self, cfg: FactoryConfig, core: Callable[[str, dict], dict], external: Checker,
                 launch: Callable, token_for: Callable[[str], str],
                 clock: Callable[[], datetime] = utc_now, log: Callable[[str], None] = print,
                 hooks: HookRunner | None = None, avail: Availability | None = None) -> None:
        self.cfg, self.o, self.core, self.external = cfg, cfg.orchestrator, core, external
        self.launch, self.token_for, self.clock, self.log = launch, token_for, clock, log
        self.hooks, self.avail, self.down = hooks, avail, {}  # down: server -> problem
        self.versions: dict[int, Workflow] = {}
        self.synced: dict[int, datetime] = {}  # task id -> last check_sync
        # state per step run id: claim token, first/last try, child [Popen, started, identity, seen claimed]
        self.claims, self.first_try, self.last_try, self.children = {}, {}, {}, {}
        self.failed, self.stalled_here = Counter(), set()

    def tick(self) -> None:
        try:
            steps = self.core("sweep", {})["steps"]
        except Exception as e:  # noqa: BLE001 - one line per failure; the loop goes on
            return self.log(f"sweep failed: {type(e).__name__}: {e}")
        self._guard("availability check", lambda: (self.hooks and self.hooks.reload(), self._availability()))
        self._reap({s["step_id"]: s for s in steps})
        for s in steps:
            for act in (self._stall, self._code if s["kind"] == "code" else self._push):  # stalled: no push
                try:
                    act(s)
                except Exception as e:  # noqa: BLE001
                    self.log(f"{s['step']} (step {s['step_id']}): {type(e).__name__}: {e}")
        if self.hooks:
            self._guard("hooks", lambda: self.hooks.tick(
                lambda after: self.core("list_events", {"after_id": after}), self.down.__contains__))
        if self.cfg.sync:
            self._guard("sync", self._sync)

    def _guard(self, what: str, act: Callable[[], object]) -> None:
        try:
            act()
        except Exception as e:  # noqa: BLE001
            self.log(f"{what} failed: {type(e).__name__}: {e}")

    def _availability(self) -> None:
        down = self.avail.problems() if self.avail else {}
        if down != self.down:
            self.log(f"not available: {describe(down)}" if down else "all servers available")
        self.down = down

    def _workflow(self, version: int | None = None) -> Workflow:
        """A version's workflow from the core, cached; None: the live one, fetched."""
        if version not in self.versions:
            view = self.core("get_workflow", {} if version is None else {"version": version})
            self.versions[view["version"]] = parse_workflow(view["data"], view["version"])
            version = view["version"]
        return self.versions[version]

    def needed(self) -> dict[str, set[str]]:
        """server -> tools the live workflow's code steps call; hook servers only need to answer."""
        extra = {h.call[0]: set() for h in self.hooks.hooks if h.call} if self.hooks else {}
        return needed_tools(self._workflow(), ("call",), extra=extra)

    def _sync(self) -> None:
        now, every = self.clock(), timedelta(minutes=min(c.every_minutes for c in self.cfg.sync.values()))
        for t in self.core("list_tasks", {})["tasks"]:
            if now - self.synced.get(t["id"], now - every) >= every:
                self.synced[t["id"]] = now
                self.core("check_sync", {"task_id": t["id"]})

    def _reap(self, live: dict[int, dict]) -> None:
        for sid, child in list(self.children.items()):
            (proc, started, identity, seen), s = child, live.get(sid)
            seen = child[3] = seen or bool(s and s["claimed_by"] == identity)
            if s and proc.poll() is None:
                if self.clock() - started <= timedelta(minutes=self.cfg.lease_minutes):
                    continue
                proc.kill()
            self.failed[sid] += not seen
            del self.children[sid]

    def _code(self, s: dict) -> None:
        sid, now = s["step_id"], self.clock()
        call = self._workflow(s["workflow_version"]).step(s["step"]).call
        if call.mcp_server in self.down:  # wait for the server (re-checked once a minute), keeping our claim
            if s["claimed_by"] == self.o.identity and sid in self.claims:
                self.core("heartbeat", {"step_id": sid, "claim_token": self.claims[sid]})
            return
        if s["status"] == "open":
            self.claims[sid] = self.core("claim_step", {"step_id": sid})["claim_token"]
            self.first_try.setdefault(sid, now)
        elif s["claimed_by"] != self.o.identity or sid not in self.claims:
            return  # someone else's claim, or ours from before a restart: wait for the lease
        if sid in self.last_try and now - self.last_try[sid] < timedelta(seconds=self.o.code_retry_seconds):
            return
        self.last_try[sid], claim = now, {"step_id": sid, "claim_token": self.claims[sid]}
        try:
            self.core("heartbeat", claim)
            ctx = self.core("get_step_context", {"step_id": sid})
            arts = {a["name"]: Artifact(**a) for a in ctx["artifacts"]}
            result = self.external(call.mcp_server, call.tool, substitute(call.args, {}, arts, ctx["values"]))
            evidence = {k: lookup(result, p) for k, p in call.evidence.items()}
            if missing := [k for k, v in evidence.items() if v is None]:
                return self.log(f"{s['step']}: no {missing[0]} in the result")
            done = self.core("complete_step", {**claim, "evidence": evidence})
        except ToolRejected as e:
            if any(w in str(e) for w in LOST):
                del self.claims[sid]
            raise
        self.log(f"{s['step']} (step {sid}) completed: {done['waiting_for']}")
        for state in (self.claims, self.first_try, self.last_try):
            state.pop(sid, None)

    def _push(self, s: dict) -> None:
        sid, identity = s["step_id"], s["push_identity"]
        k, cap = s["lease_expiries"] + self.failed[sid], self.o.max_lease_expiries
        if (s["dispatch"] != "push" or s["status"] != "open" or sid in self.children
                or k >= cap and (sid in self.stalled_here or self.failed[sid] >= cap)):  # rule 2 stops pushes
            return
        shas = [a["commit"] for a in self.core("get_step_context", {"step_id": sid})["artifacts"]]
        try:
            proc, log = self.launch(sid, self.token_for(identity), shas)
        except (headless.StartFailed, TokenError):
            self.failed[sid] += 1
            raise
        self.children[sid] = [proc, self.clock(), identity, False]
        self.log(f"started {s['step']} (step {sid}) as {identity}: {log}")

    def _stall(self, s: dict) -> None:
        sid, now, n = s["step_id"], self.clock(), self.o.stall_after_minutes
        if s["stalled"] or sid in self.stalled_here:
            return
        k = s["lease_expiries"] + self.failed[sid]
        if s["status"] == "open" and now - datetime.fromisoformat(s["idle_since"]) > timedelta(minutes=n):
            reason = f"nobody picked it up for {n} min"
        elif k >= self.o.max_lease_expiries:
            reason = f"lease expired or start failed {k} times"
        elif sid in self.first_try and now - self.first_try[sid] > timedelta(minutes=n):
            reason = f"code step not through after {n} min"
        else:
            return
        self.core("report_stall", {"step_id": sid, "reason": reason})
        self.stalled_here.add(sid)
        self.log(f"{s['step']} (step {sid}) stalled: {reason}")


def run(cfg: FactoryConfig) -> None:
    o, dotenv, state = cfg.orchestrator, read_dotenv(cfg.env_file), cfg.root / ".taktloop"
    log = partial(print, flush=True)
    external = make_checker(cfg.mcp_servers, os.environ, dotenv)
    secrets = [s.token_env for s in cfg.mcp_servers.values()]  # no token reaches a hook
    hooks = HookRunner(cfg.hooks_path, state / "hooks.cursor", cfg.mcp_servers.keys(), external,
                       partial(run_command, cwd=cfg.root, environ=os.environ, secret_envs=secrets), log)
    orch = Orchestrator(
        cfg, CoreClient(cfg.url, o.identity, cfg.credentials).call, external,
        lambda sid, token, shas: headless.start(sid, token, cfg.product_checkout, os.environ, shas, cfg.root),
        partial(fetch_token, credentials_dir=cfg.credentials), log=log, hooks=hooks)
    orch.avail = Availability(make_tool_lister(cfg.mcp_servers, os.environ, dotenv), orch.needed)
    print(f"taktloop orchestrator for factory '{cfg.factory}' as {o.identity}", flush=True)
    with suppress(KeyboardInterrupt):
        while True:
            orch.tick()
            time.sleep(o.tick_seconds)

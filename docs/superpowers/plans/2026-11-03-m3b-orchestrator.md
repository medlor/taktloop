# M3b: the orchestrator — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A separate `taktloop orchestrator` process runs code steps (`verify` finds its own CI run), starts a read-only headless Claude reviewer for `code_review`, drives lease expiry on a timer and flags stalled steps, while the core keeps the hard-gate check.

**Architecture:** The core gains `kind: code` and `dispatch: push` in the workflow, two orchestrator-only tools (`sweep`, `report_stall`) and a *stalled* suffix on `waiting_for`. A new `orchestrator.py` is an MCP client of the core with its own identity: each tick it calls `sweep` (which expires overdue leases as every operation already does), claims and runs code steps with its own GitHub credential (reusing `external.make_checker`), starts headless runs through `headless.py`, and reports stalls. All decisions about state stay in the core.

**Tech Stack:** Python ≥ 3.12, uv, `mcp` ≥ 2.3, `pyyaml`, `pyjwt[crypto]`, SQLite, pytest; Claude Code CLI (`claude`) for headless runs.

**Spec:** `docs/superpowers/specs/2026-11-03-m3b-orchestrator-design.md` (approved). The M1, M2 and M3a designs still hold where it is silent.

## Global Constraints

- Line budget: `wc -l src/taktloop/*.py src/taktloop/issuer/*.py | tail -1` **≤ 2,900** (today 2,472, so **≤ +428**). No module over ~300 lines. Each task names its allowance; put the running total in each commit message body (`src total: N`).
- Direct runtime dependencies unchanged (`mcp`, `pyyaml`, `pyjwt[crypto]`); child processes via `subprocess`.
- uv only (`uv run pytest`, `uv run taktloop …`). English docs; product name "taktloop".
- The core never acts on the outside world for code steps; the orchestrator never decides a hard gate. `complete_step`'s check path (M3a) is unchanged.
- No network call while a SQLite transaction is open.
- No GitHub token and no taktloop token in logs, events or run logs.
- Defaults (spec §4): `tick_seconds: 15`, `code_retry_seconds: 60`, `stall_after_minutes: 60`, `max_lease_expiries: 2`.
- Headless start: `claude -p "work on taktloop step <step_id>"`, cwd = the directory of `factory.yaml`, `--permission-mode dontAsk`, `--allowedTools Read Grep Glob Skill "Bash(git diff:*)" "Bash(git log:*)" "Bash(git show:*)" "Bash(git rev-parse:*)" "mcp__taktloop__*"`, `--disallowedTools Edit Write NotebookEdit`. Log to `.taktloop/runs/<step_id>-<n>.log`.
- Claude auth: `claude auth status` JSON with `loggedIn: true` and `authMethod: "claude.ai"` → drop `ANTHROPIC_API_KEY` from the child env; else keep `ANTHROPIC_API_KEY` if set; else the start fails ("no Claude login and no ANTHROPIC_API_KEY").
- GitHub `actions_list` facts (spec §4, live): args `method: list_workflow_runs`, `owner`, `repo`, `resource_id: ci.yml`, `workflow_runs_filter: {branch: …}`; result `{"workflow_runs": {"total_count", "workflow_runs": [ {id, head_sha, status, conclusion, …}, … ]}}`, newest first.

## Review Focus

1. **Orchestrator restarted while it holds a code step's claim** (claim token lost): the tick must skip that step without error until the lease expires, then claim it again. Pinned by `test_tick_skips_own_claim_without_token` (Task 5).
2. **Core down or token expired mid-run:** a tick logs one line and the loop goes on; a 401 fetches a new token once. Pinned by `test_tick_survives_core_down` and `test_client_refreshes_token_on_401` (Task 5).
3. **The step moves on under the orchestrator** (someone force-released it, or the task was sent back): `complete_step`/`heartbeat` is refused "not claimed" → the orchestrator forgets its claim and does not retry with the dead token. Pinned by `test_tick_forgets_lost_claim` (Task 5).
4. **`ANTHROPIC_API_KEY` set while a subscription login exists**, and **`claude` not on PATH**: key dropped in the first case; a failed start (counted, logged), not a crash, in the second. Pinned by `test_child_env_*` and `test_claude_auth_missing_binary` (Task 4).
5. **Two tasks waiting at `verify` at once:** retry timers, claims and stall timers are per step run. Pinned by `test_tick_two_code_steps_independent` (Task 5).

---

### Task 1: Code-step calls in the check machinery

Allowance: ~+25 lines (`checks.py`).

**Files:**
- Modify: `src/taktloop/checks.py`, `src/taktloop/external.py` (type alias only)
- Test: `tests/test_checks.py`

**Interfaces:**
- Produces:
  - `CheckDecl.args: Mapping[str, Any]` — nested mappings/lists kept; non-string scalars kept as they are (spec §5.1). `match`/`expect` stay `Mapping[str, str]`.
  - `CallDecl` frozen dataclass: `mcp_server: str`, `tool: str`, `args: Mapping[str, Any]`, `evidence: Mapping[str, str]` (evidence key → dotted result path).
  - `parse_call(raw: Any) -> CallDecl` — fields exactly `mcp_server`, `tool`, `args`, `evidence`; `mcp_server`/`tool` non-empty strings; `evidence` a non-empty mapping of strings. Raises `ValueError` (no step prefix), like `parse_check`.
  - `substitute(value: Any, evidence: Mapping[str, str], artifacts: Mapping[str, Artifact]) -> Any` — replaces placeholders in every string at any depth; `resolve` uses it for `args`.
  - `placeholders(decl: CheckDecl | CallDecl) -> set[str]` — walks nested `args` (and `match`/`expect` for checks).
  - `lookup(result, path)` — a path segment that is a non-negative integer indexes a list; out of range → `None`.
  - `external.Checker = Callable[[str, str, dict[str, Any]], dict]`.

- [ ] **Step 1: Write the failing tests** in `tests/test_checks.py`

```python
def test_parse_check_keeps_nested_args():
    d = parse_check({"mcp_server": "github", "tool": "actions_list",
                     "args": {"page": 1, "workflow_runs_filter": {"branch": "{{artifacts.change.branch}}"}},
                     "expect": {"x": "y"}})
    assert d.args == {"page": 1, "workflow_runs_filter": {"branch": "{{artifacts.change.branch}}"}}
    assert placeholders(d) == {"artifacts.change.branch"}

def test_parse_call():
    c = parse_call({"mcp_server": "github", "tool": "actions_list", "args": {"a": "b"},
                    "evidence": {"ci_run": "workflow_runs.workflow_runs.0.id"}})
    assert c.evidence == {"ci_run": "workflow_runs.workflow_runs.0.id"}
    for bad in ({"tool": "t", "evidence": {"k": "p"}}, {"mcp_server": "g", "tool": "t"},
                {"mcp_server": "g", "tool": "t", "evidence": {"k": "p"}, "expect": {}}):
        with pytest.raises(ValueError):
            parse_call(bad)

def test_substitute_nested():
    arts = {"change": Artifact(name="change", path=".", commit="c" * 40, branch="taktloop/1-x",
                               step="build", round=1)}
    assert substitute({"f": {"branch": "{{artifacts.change.branch}}"}, "n": 3, "l": ["{{artifacts.change.commit}}"]},
                      {}, arts) == {"f": {"branch": "taktloop/1-x"}, "n": 3, "l": ["c" * 40]}

def test_lookup_indexes_lists():
    r = {"workflow_runs": {"workflow_runs": [{"id": 7}, {"id": 6}]}}
    assert lookup(r, "workflow_runs.workflow_runs.0.id") == "7"
    assert lookup(r, "workflow_runs.workflow_runs.5.id") is None
    assert lookup({"workflow_runs": {"workflow_runs": []}}, "workflow_runs.workflow_runs.0.id") is None
```

Delete the M3a test `test_parse_check_stringifies_values` (its `"page": "1"` assertion contradicts spec §5.1).

- [ ] **Step 2: Run** `uv run pytest tests/test_checks.py -v` — Expected: the new tests FAIL (`ImportError: parse_call`).
- [ ] **Step 3: Implement** the interfaces above in `checks.py`. `args` validation: must be a mapping if present; values unrestricted. `_strings` stays for `match`/`expect`.
- [ ] **Step 4: Run** `uv run pytest -q` — Expected: all pass (the M3a check tests, `test_service_checks.py` and `test_e2e_m3a.py` still pass unchanged).
- [ ] **Step 5: Commit** `git commit -m "Let check and call args nest; index lists in result paths"` (body: `src total: N`).

---

### Task 2: `kind: code`, `dispatch: push` and the `orchestrator:` config

Allowance: ~+55 lines (`workflow.py` ~+38, `config.py` ~+17).

**Files:**
- Modify: `src/taktloop/workflow.py`, `src/taktloop/config.py`
- Test: `tests/test_workflow.py`, `tests/test_config.py`

**Interfaces:**
- Consumes: `CallDecl`, `parse_call`, `placeholders` (Task 1).
- Produces:
  - `KINDS = ("agent", "human_gate", "code")`; `Step.kind: Literal["agent", "human_gate", "code"]`.
  - `Step` gains `call: CallDecl | None = None`, `dispatch: Literal["pull", "push"] = "pull"`, `push_identity: str | None = None`.
  - Code step rules: needs `role`, `call`, `next`; may have `check` + `evidence` + `on_fail` (M3a's together-rule); refuses `skill`, `output`, review keys, `dispatch`/`push` ("code steps take no …"). `Step.evidence` = the step's `evidence:` list if given (must equal `set(call.evidence)`, else "call.evidence must produce exactly the step's evidence keys"), else `tuple(call.evidence)`. `call` placeholders may only be `artifacts.<name>.commit|branch|path` (no `evidence.*`: the call produces the evidence).
  - Push rules: `dispatch` is `pull` (default) or `push`, agent steps only; `push` requires `push: {start: claude-headless, identity: <non-empty>}`; another `start` → "push.start 'x' is not supported (supported: claude-headless)".
  - `config.OrchestratorConfig` frozen dataclass: `identity: str`, `tick_seconds: int = 15`, `code_retry_seconds: int = 60`, `stall_after_minutes: int = 60`, `max_lease_expiries: int = 2`.
  - `FactoryConfig` gains `orchestrator: OrchestratorConfig | None = None` and `root: Path = Path(".")` (the directory of `factory.yaml`). `orchestrator:` without `identity` is refused. A code step's `call.mcp_server` must be in `mcp_servers` (same message shape as for `check`).

- [ ] **Step 1: Write the failing tests**

`tests/test_workflow.py` (reuse the file's step-building helpers):
- `test_code_step_loads`: a `verify` code step as in spec §4 → `kind == "code"`, `call.tool == "actions_list"`, `evidence == ("ci_run",)`, `check` set, `targets` include `on_fail`.
- `test_code_step_refusals`, parametrized over: `skill` present; `output` present; `next` missing; `call` missing; `evidence: [other]` vs `call.evidence: {ci_run: …}`; a call arg `"{{evidence.ci_run}}"`; `dispatch: push` on a code step. Each raises `WorkflowError` whose message names the step.
- `test_push_step_loads`: `code_review` with `dispatch: push`, `push: {start: claude-headless, identity: reviewer-bot}` → `dispatch == "push"`, `push_identity == "reviewer-bot"`.
- `test_push_refusals`: `push.start: codex` (message contains `"not supported"`); missing `push.identity`; `dispatch: push` on a human gate; `dispatch: sometimes`.

`tests/test_config.py`:
- `test_orchestrator_block`: defaults fill in (`tick_seconds == 15`, `code_retry_seconds == 60`, `stall_after_minutes == 60`, `max_lease_expiries == 2`); `root == path.parent`; no block → `orchestrator is None`; block without `identity` → `WorkflowError`.
- `test_code_step_unknown_server`: `call.mcp_server: gitlab` → `WorkflowError` mentioning `gitlab`.

- [ ] **Step 2: Run** `uv run pytest tests/test_workflow.py tests/test_config.py -v` — Expected: new tests FAIL.
- [ ] **Step 3: Implement** in `workflow.py` (a `_parse_code_step` helper next to `_parse_step`; `_check_placeholders` covers `call`) and `config.py`.
- [ ] **Step 4: Run** `uv run pytest -q` — Expected: all pass.
- [ ] **Step 5: Commit** `git commit -m "Load code steps, pushed steps and the orchestrator block"`.

---

### Task 3: Core support — code steps, `sweep`, `report_stall`, stalled waiting-for

Allowance: ~+100 lines (new `ops_orchestrator.py` ~55, `models.py` ~15, `mcp_server.py` ~12, `rules.py` ~8, `ops_base.py` ~6, `service.py`/`ops_steps.py` ~2, `permissions.py` 1, `cli_render.py` ~2).

**Files:**
- Create: `src/taktloop/ops_orchestrator.py`
- Modify: `src/taktloop/service.py`, `ops_base.py`, `ops_steps.py`, `rules.py`, `permissions.py`, `models.py`, `mcp_server.py`, `cli_render.py`
- Test: `tests/test_service_orchestrator.py` (new), `tests/test_permissions.py`, `tests/test_mcp_server.py`

**Interfaces:**
- Consumes: `Step.kind == "code"`, `Step.dispatch`, `Step.push_identity` (Task 2).
- Produces:
  - `models.SweptStep`: `step_id: int`, `task_id: int`, `step: str`, `kind: str`, `role: str`, `dispatch: str`, `push_identity: str | None`, `status: str` (`open`|`claimed`), `claimed_by: str | None` (subject only), `idle_since: datetime`, `lease_expiries: int`, `stalled: bool`. `models.SweptStepList(steps: list[SweptStep])`.
  - `Service.sweep(caller) -> list[SweptStep]` — every step run with status `open` or `claimed`, ordered by id; read-only (`writes=False`). Overdue leases are already expired by `_run` before the body (existing behaviour), so a `sweep` after the lease end shows the run `open` with `lease_expiries` +1.
    - `idle_since` = the latest `ok` `lease_expired` or `release_step` event `at` on that run, else `opened_at`.
    - `lease_expiries` = number of `lease_expired` events on that run.
  - `Service.report_stall(caller, step_id: int, reason: str) -> TaskSummary` — `reason` via `clean_reason`; the run must be `open` or `claimed` (else `Rejected("step is not open (status: …)")`). First report on a run: records the caller's own `report_stall` event with detail `{"step_id", "reason"}`. A second report on the same run: no event, same summary (spec §7 "accepted and ignored"). Implemented with `_run(..., writes=False)` and an explicit `self._event(...)` in the body. (The spec's "escalate event by the orchestrator" is this event; `escalate` stays the review-escalation event.)
  - `ops_base._stall_reason(tx, run) -> str | None` — the reason of the `ok` `report_stall` event on `run.id`, if any; used by `_summary` and `sweep`.
  - `rules.waiting_for(..., stalled: str | None = None)` — code step open: `"waiting for the orchestrator to run {step.name}"`; any text gets `f" — stalled: {stalled}"` appended when `stalled`.
  - `permissions.ROLES`: `"sweep": "orchestrator"`, `"report_stall": "orchestrator"`.
  - `claim_step` accepts `kind in ("agent", "code")` (the gate refusal text stays for human gates). `list_open_steps` lists `agent` and `code` steps for holders of the role; `OpenStep.skill` is `""` for code steps.
  - MCP tools `sweep(ctx) -> SweptStepList` and `report_stall(ctx, step_id: int, reason: str) -> TaskSummary`.
  - `cli_render._event_detail`: `report_stall` → `f"stalled: {d['reason']}"`.

- [ ] **Step 1: Write the failing tests** in `tests/test_service_orchestrator.py`, using a workflow fixture with a `build` agent step → `verify` code step (role `verifier`, the spec §4 call and check) → `code_review` pushed review step → … ; callers `ORCH = Caller("orchestrator", frozenset({"orchestrator", "verifier"}))`, `AGENT = Caller("agent-a", frozenset({"builder", "reviewer"}))`, the `clock` fixture, `lease=timedelta(minutes=30)`.

```python
def test_sweep_lists_open_and_claimed(svc_m3b): ...
    # one task at build: sweep(ORCH) == [SweptStep(step="build", kind="agent", status="open", dispatch="pull",
    #   lease_expiries=0, stalled=False, idle_since=<opened_at>, ...)]

def test_sweep_expires_and_counts(svc_m3b, clock):
    # AGENT claims build; clock += 31 min; s = sweep(ORCH)[0]
    assert (s.status, s.lease_expiries, s.idle_since) == ("open", 1, clock.now)
    # task history has one lease_expired event by core

def test_sweep_requires_orchestrator_role(svc_m3b):
    with pytest.raises(Rejected, match="sweep requires role orchestrator"): svc_m3b.sweep(AGENT)

def test_report_stall_once(svc_m3b):
    s = svc_m3b.report_stall(ORCH, step_id, "nobody picked it up for 60 min")
    assert s.waiting_for.endswith(" — stalled: nobody picked it up for 60 min")
    svc_m3b.report_stall(ORCH, step_id, "again")
    events = [e for e in history if e.operation == "report_stall"]
    assert [(e.subject, e.detail["reason"]) for e in events] == [("orchestrator", "nobody picked it up for 60 min")]
    assert svc_m3b.sweep(ORCH)[0].stalled is True

def test_report_stall_refusals(svc_m3b):
    # blank reason -> "reason is required"; closed run -> "step is not open"; AGENT -> role refusal

def test_code_step_claimable_and_listed(svc_m3b):
    # task at verify: list_open_steps(ORCH) has verify with skill ""; list_open_steps(AGENT) has none;
    # waiting_for == "waiting for the orchestrator to run verify"; ORCH claims it: ok
```

Add `"sweep"` and `"report_stall"` to the role table test in `tests/test_permissions.py`, and one in-memory MCP test in `tests/test_mcp_server.py` calling `sweep` under `as_identity("orchestrator", "orchestrator")`.

- [ ] **Step 2: Run** `uv run pytest tests/test_service_orchestrator.py tests/test_permissions.py tests/test_mcp_server.py -v` — Expected: new tests FAIL.
- [ ] **Step 3: Implement** `OrchestratorOps(ServiceBase)` in `ops_orchestrator.py` (`Service` inherits it next to `StepOps`, `GateOps`), plus the other interfaces above.
- [ ] **Step 4: Run** `uv run pytest -q` — Expected: all pass.
- [ ] **Step 5: Commit** `git commit -m "Give the core code steps, sweep and stall reports"`.

---

### Task 4: Headless starts

Allowance: ~+65 lines (new `headless.py`).

**Files:**
- Create: `src/taktloop/headless.py`
- Test: `tests/test_headless.py`

**Interfaces:**
- Produces:
  - `class StartFailed(Exception)` — message is the reason (no secrets).
  - `READ_ONLY_TOOLS: tuple[str, ...]`, `DENIED_TOOLS: tuple[str, ...]` — exactly the Global Constraints lists.
  - `claude_auth(run: Callable = subprocess.run) -> dict | None` — `claude auth status` → parsed JSON; missing binary, non-zero exit or bad JSON → `None`.
  - `child_env(environ: Mapping[str, str], auth: dict | None, token: str) -> dict[str, str]` — copy of `environ` with `TAKTLOOP_TOKEN=token`; the auth rule from Global Constraints; raises `StartFailed("no Claude login and no ANTHROPIC_API_KEY")`.
  - `command(step_id: int) -> list[str]` — `["claude", "-p", f"work on taktloop step {step_id}", "--permission-mode", "dontAsk", "--allowedTools", *READ_ONLY_TOOLS, "--disallowedTools", *DENIED_TOOLS]`.
  - `ensure_commits(root: Path, shas: Iterable[str], run: Callable = subprocess.run) -> None` — if any `git cat-file -e <sha>^{commit}` fails, run `git fetch origin` once (failure is ignored; the reviewer will report a missing commit itself).
  - `start(step_id: int, token: str, root: Path, environ: Mapping[str, str], shas: Iterable[str]) -> tuple[subprocess.Popen, Path]` — `ensure_commits`, `child_env(environ, claude_auth(), token)`, then `Popen(command(step_id), cwd=root, env=…, stdout=log, stderr=STDOUT, stdin=DEVNULL)`; log path `root/.taktloop/runs/<step_id>-<n>.log` with the first free `n` starting at 1. `FileNotFoundError` for `claude` → `StartFailed("claude is not on PATH")`.

- [ ] **Step 1: Write the failing tests** in `tests/test_headless.py`

```python
SUB = {"loggedIn": True, "authMethod": "claude.ai"}
def test_child_env_subscription_drops_key():
    env = child_env({"ANTHROPIC_API_KEY": "sk-x", "PATH": "/bin"}, SUB, "tok")
    assert "ANTHROPIC_API_KEY" not in env and env["TAKTLOOP_TOKEN"] == "tok" and env["PATH"] == "/bin"
def test_child_env_falls_back_to_key():
    assert child_env({"ANTHROPIC_API_KEY": "sk-x"}, {"loggedIn": False}, "t")["ANTHROPIC_API_KEY"] == "sk-x"
    assert child_env({"ANTHROPIC_API_KEY": "sk-x"}, None, "t")["ANTHROPIC_API_KEY"] == "sk-x"
def test_child_env_neither_fails():
    with pytest.raises(StartFailed, match="no Claude login and no ANTHROPIC_API_KEY"):
        child_env({}, {"loggedIn": False}, "t")
    with pytest.raises(StartFailed):
        child_env({}, None, "t")
def test_claude_auth_missing_binary():
    def run(*a, **k): raise FileNotFoundError
    assert claude_auth(run) is None
def test_command_is_read_only():
    cmd = command(42)
    assert cmd[:3] == ["claude", "-p", "work on taktloop step 42"]
    assert cmd[cmd.index("--permission-mode") + 1] == "dontAsk"
    assert "Edit" in cmd[cmd.index("--disallowedTools"):] and "Edit" not in cmd[:cmd.index("--disallowedTools")]
def test_ensure_commits_fetches_once_when_missing(): ...  # fake run: cat-file fails → exactly one ["git", "fetch", "origin"]
def test_start_writes_numbered_logs(tmp_path, monkeypatch):
    # monkeypatch headless.claude_auth -> SUB and headless.command -> [sys.executable, "-c", "print('hi')"];
    # two starts for step 7 -> logs .taktloop/runs/7-1.log and 7-2.log, both contain "hi"
def test_start_without_claude(tmp_path, monkeypatch):
    # command -> ["/nonexistent/claude"] -> StartFailed("claude is not on PATH")
```

- [ ] **Step 2: Run** `uv run pytest tests/test_headless.py -v` — Expected: FAIL (`ModuleNotFoundError`).
- [ ] **Step 3: Implement** `headless.py`.
- [ ] **Step 4: Run** `uv run pytest -q` — Expected: all pass.
- [ ] **Step 5: Commit** `git commit -m "Start read-only headless Claude runs with the right auth"`.

---

### Task 5: The orchestrator loop and `taktloop orchestrator`

Allowance: ~+165 lines (new `orchestrator.py` ~150, `cli.py`/`cli_commands.py` ~15).

**Files:**
- Create: `src/taktloop/orchestrator.py`
- Modify: `src/taktloop/cli.py`, `src/taktloop/cli_commands.py`
- Test: `tests/test_orchestrator.py` (new), `tests/test_cli.py`

**Interfaces:**
- Consumes: `sweep`, `report_stall`, `claim_step`, `heartbeat`, `get_step_context`, `complete_step` (core tools); `substitute`, `lookup` (Task 1); `OrchestratorConfig`, `FactoryConfig.root` (Task 2); `headless.start`, `StartFailed` (Task 4); `external.make_checker`, `read_dotenv` (M3a); `cli_commands._call_tool`, `ToolRejected`, `TokenRejected`, `CoreUnreachable`; `token_client.fetch_token`.
- Produces:
  - `cli_commands._call_tool(url, name, args, token, client: str = "taktloop-cli")` — `client` becomes the `clientInfo` name (the orchestrator passes `"taktloop-orchestrator"`).
  - `class CoreClient` — `__init__(self, url: str, identity: str, credentials: Path)`; `call(self, tool: str, args: dict) -> dict` (sync, `anyio.run`); on `TokenRejected` fetches a new token once and retries (this covers spec §6.1's refresh near expiry too: the 12 h token is refreshed when the core first refuses it); raises `ToolRejected`/`CoreUnreachable` unchanged.
  - `class Orchestrator` — `__init__(self, cfg: FactoryConfig, core: Callable[[str, dict], dict], external: Checker, launch: Callable[[int, str, list[str]], tuple[subprocess.Popen, Path]], token_for: Callable[[str], str], clock: Callable[[], datetime] = utc_now, log: Callable[[str], None] = print)`. `launch(step_id, token, shas)` is `headless.start` bound to `cfg.root` and `os.environ` in production.
  - `Orchestrator.tick(self) -> None` — one pass, never raises: any `CoreUnreachable`/`TokenError`/`ToolRejected` from `sweep` logs one line and returns.
  - `run(cfg: FactoryConfig) -> None` — builds the client, checker (`make_checker(cfg.mcp_servers, os.environ, read_dotenv(cfg.env_file))`) and launcher, then `tick()` every `tick_seconds` until Ctrl-C; prints `taktloop orchestrator for factory '<f>' as <identity>` at start.
  - CLI: `taktloop orchestrator [--config factory.yaml]` → `run(cfg)`; a config without an `orchestrator:` block → exit 2, `taktloop: factory.yaml has no orchestrator block`.

**Tick algorithm** (state in memory, keyed by step run id: `claims` (claim token), `first_try`, `last_try`, `children` (Popen, started at, push identity), `seen_claimed` (bool), `failed_starts` (int), `stalled_here` (set)):

1. `steps = core("sweep", {})["steps"]`.
2. Children: for each child whose step is no longer open-or-claimed, or has exited: if it exited and `seen_claimed` is false, `failed_starts += 1`. A child running longer than `cfg.lease_minutes` is killed. Drop finished children. For each swept step `claimed_by == push_identity` set `seen_claimed`.
3. For each swept step, in order:
   - **code** (`kind == "code"`): if `open` → `claim_step`, keep the token, set `first_try`. If `claimed_by == identity` and no token in memory → skip (Review Focus 1). If claimed by us and `now - last_try >= code_retry_seconds` (or never tried): `heartbeat`, `get_step_context`, `args = substitute(call.args, {}, {a["name"]: Artifact(**a) for a in ctx["artifacts"]})`, `result = external(call.mcp_server, call.tool, args)`, `evidence = {k: lookup(result, p) for k, p in call.evidence.items()}`; a `None` value → log `"<step>: no <key> in the result"` and wait; else `complete_step(evidence=…)`. `ToolRejected` containing `"not claimed"` or `"claimed by"` → forget the claim (Review Focus 3); other `ToolRejected`/`CheckUnavailable` → log and wait. A completion that returns → forget all state of that run.
   - **push** (`dispatch == "push"`, `status == "open"`, no live child, not in `stalled_here`): `launch(step_id, token_for(push_identity), shas)` where `shas` = the commits of the step context's artifacts; log `started <step> (step <id>) as <identity>: <log path>`. `StartFailed`/`TokenError` → log, `failed_starts += 1`.
   - **stall** (`not stalled`): reason, first match wins —
     1. `status == "open"` and `now - idle_since > stall_after_minutes` → `"nobody picked it up for <n> min"`;
     2. `lease_expiries + failed_starts >= max_lease_expiries` → `"lease expired or start failed <k> times"`;
     3. code step with `first_try` older than `stall_after_minutes` → `"code step not through after <n> min"`.
     Then `report_stall(step_id, reason)` and add to `stalled_here`.

The orchestrator reads `call` declarations from `cfg.workflow.step(swept["step"]).call` (same `factory.yaml` as the core).

- [ ] **Step 1: Write the failing tests** in `tests/test_orchestrator.py` against a **real in-process `Service`** with the Task 3 test workflow: `core = lambda tool, args: <dispatch to svc methods as ORCH, returning model_dump(mode="json")>`, raising `ToolRejected(reason)` on `Rejected`. `external` is a fake returning canned `actions_list` results; `launch` is a fake recording calls and returning objects with `poll()`/`kill()`; `clock` is the `Clock` fixture; `log` appends to a list.

```python
def test_code_step_refused_then_passes(...):
    # task at verify, change commit C; fake runs: newest = {id: 101, head_sha: C, status: in_progress}
    orch.tick()  # claims, calls, completes -> core check refuses (status), claim kept
    assert task.current_step == "verify"
    clock.now += timedelta(seconds=30); orch.tick()   # within code_retry_seconds: no call
    assert len(external.calls) == 1
    runs set to completed/success; clock.now += timedelta(seconds=31); orch.tick()
    assert task.current_step == "<next>"
    # external.calls[0] args == {"method": "list_workflow_runs", "owner": "medlor", "repo": "taktloop",
    #   "resource_id": "ci.yml", "workflow_runs_filter": {"branch": BRANCH}}
def test_code_step_red_run_goes_to_on_fail(...)
def test_code_step_missing_run_waits(...)          # empty workflow_runs list -> no complete_step, log names ci_run
def test_tick_two_code_steps_independent(...)      # Review Focus 5
def test_tick_skips_own_claim_without_token(...)   # Review Focus 1: fresh Orchestrator, step claimed by "orchestrator"
def test_tick_forgets_lost_claim(...)              # Review Focus 3: force-release between ticks
def test_push_started_once(...)                    # open pushed step: launch called once over 3 ticks while child alive
def test_push_failed_starts_stall_and_stop(...)    # launch raises StartFailed twice -> report_stall
                                                   # "lease expired or start failed 2 times"; third tick: no launch
def test_stall_nobody_picked_up(...)               # human gate idle 61 min -> one report_stall, not repeated next tick
def test_stall_code_step_not_through(...)          # refused for 61 min -> "code step not through after 60 min"
def test_tick_survives_core_down(...)              # core raises CoreUnreachable -> tick returns, one log line
def test_client_refreshes_token_on_401(monkeypatch) # CoreClient: _call_tool raises TokenRejected once -> fetch_token called twice, result returned
```

In `tests/test_cli.py`: `test_orchestrator_needs_block` (a factory without `orchestrator:` → exit 2 with the message above).

- [ ] **Step 2: Run** `uv run pytest tests/test_orchestrator.py tests/test_cli.py -v` — Expected: new tests FAIL.
- [ ] **Step 3: Implement** `orchestrator.py`, the `_call_tool` `client` parameter and the CLI command (lazy import, like `serve`).
- [ ] **Step 4: Run** `uv run pytest -q` — Expected: all pass. Check `wc -l src/taktloop/*.py src/taktloop/issuer/*.py | tail -1` ≤ 2,900.
- [ ] **Step 5: Commit** `git commit -m "Run the orchestrator: code steps, pushed reviewers, stalls"`.

---

### Task 6: Ship it — configs, identities, bridge skill, end-to-end test, docs

Allowance: 0 `src/` lines.

**Files:**
- Modify: `factory.yaml`, `templates/mvp/factory.yaml` (identical workflow), `dev-issuer.yaml`, `templates/mvp/skills/taktloop/SKILL.md`, `tests/conftest.py` (fake `actions_list`), `tests/test_e2e_m3a.py`, `tests/test_templates.py`, `tests/test_external.py` (live test), `README.md`, `docs/adr/0002-external-tools-via-their-own-mcp-servers.md`, `docs/adr/0004-executors-pull-steps-via-bridge-skill.md`
- Rename: `tests/test_e2e_m3a.py` → `tests/test_e2e_m3b.py` (the demo now covers M3b)

**Content:**
- `factory.yaml` / template: the `orchestrator:` block and the `code_review`/`verify` changes exactly as spec §4 (`verify` loses `skill`/`instructions`; its `check` is unchanged).
- `dev-issuer.yaml`: `agent-a`/`agent-b` lose `taktloop:taktloop:verifier`; add `orchestrator: {scopes: [taktloop:taktloop:orchestrator, taktloop:taktloop:verifier]}` and `reviewer-bot: {scopes: [taktloop:taktloop:reviewer]}`.
- Bridge skill: a step id in the prompt ("work on taktloop step <id>") → claim that step instead of listing, never ask the user anything; `verify` is run by the orchestrator, sessions don't do it (replace the M3a verify paragraph).
- `fake_github` gains `actions_list(method, owner, repo, resource_id, workflow_runs_filter: dict | None = None) -> dict`, returning `{"method": method, "workflow_runs": {"total_count": n, "workflow_runs": [...]}}` from `github_runs` entries whose `head_branch` equals the filter's branch, newest (highest id) first; calls recorded like `actions_get`'s.

- [ ] **Step 1: Update the tests first**
  - `test_templates.py`: both factories load with `verify.kind == "code"`, `code_review.dispatch == "push"`, `push_identity == "reviewer-bot"`, `orchestrator.identity == "orchestrator"`; the bridge skill mentions `work on taktloop step`; the committed `dev-issuer.yaml` gives `verifier` only to `orchestrator`.
  - `test_e2e_m3b.py` (from `test_e2e_m3a.py`): the issuer gains the two identities; steps 1–4 as before (agent-b still pulls `code_review`: the orchestrator is not running yet). Step 5 becomes: an in-process `Orchestrator` (`CoreClient` for identity `orchestrator`, `make_checker` pointed at the fake, fake `launch`) ticks with a fake clock; with run 101 `in_progress` on the branch the history shows a refused `complete_step` by `orchestrator` (`status is in_progress`); with 102 `failure` the task goes to `build` with `check_failed` feedback; after build round 3 and review, run 103 `success` → `integration_approval`. Assert the fake saw `actions_list` with the orchestrator's GitHub token and `actions_get` with the core's (both `fake-gh-7f3a` here, since one PAT is shared, spec §6.2), and that `agent-b` is refused `claim_step` on `verify` (`requires role verifier`). Then a pushed-step check: with the build passing to `code_review`, one tick calls the fake `launch` once with `reviewer-bot`'s token. Step 7–8 as before, with `verify` completions by `orchestrator`.
  - `test_external.py` live test: add one `actions_list` / `list_workflow_runs` call with `workflow_runs_filter: {branch: "main"}` and assert `workflow_runs.workflow_runs.0.head_branch == "main"` (skipped without `TAKTLOOP_GITHUB_TOKEN`).
- [ ] **Step 2: Run** `uv run pytest tests/test_templates.py tests/test_e2e_m3b.py -v` — Expected: FAIL.
- [ ] **Step 3: Update** the configs, `dev-issuer.yaml`, bridge skill and `fake_github`.
- [ ] **Step 4: Run** `uv run pytest -q` — Expected: all pass (live test runs if the token is set).
- [ ] **Step 5: Docs:** README — start order (`issuer`, `serve`, `orchestrator`), `taktloop issuer init` for the two new identities, the headless auth rule and where run logs go; ADR 0002 — code steps use the orchestrator's credential, gate checks stay with the core; ADR 0004 — the orchestrator is a separate process using the core's MCP tools.
- [ ] **Step 6: Commit** `git commit -m "Ship M3b: orchestrator in the factory, pushed code review, verify as a code step"`, then `uv run taktloop install-skill` so the local `.claude/skills/taktloop` copy is current for the demo.

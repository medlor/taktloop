# M1 "A task survives sessions" Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A taktloop core (one process, one SQLite file) that moves one task through design → design approval → plan → done across separate Claude Code sessions, driven by the bridge skill and a CLI.

**Architecture:** `cli → mcp_server → service → (workflow, store)`; nothing points upward. `service` is the only place that changes state and writes the history (`events`). The CLI is an MCP client of the running core; the core serves MCP over streamable HTTP.

**Tech Stack:** Python ≥ 3.12, astral uv, `mcp` ≥ 2.3 (`MCPServer`, `Client`), `pyyaml`, stdlib `sqlite3`/`argparse`, pytest.

**Spec:** `docs/superpowers/specs/2026-11-01-mvp-milestones-and-m1-design.md`

## Global Constraints

- Python `>=3.12`; **uv for everything** (`uv add`, `uv run`, `uv.lock` committed). No pip, no venv by hand.
- Direct runtime dependencies: exactly `mcp>=2.3` and `pyyaml`. Dev group: `pytest`, `anyio` is already transitive (use `pytest.mark.anyio` or `anyio.run` in tests).
- `mcp` 2.x API: `from mcp.server.mcpserver import MCPServer, Context`; errors with `from mcp.server.mcpserver.exceptions import ToolError`; client `from mcp import Client`. Do **not** copy 1.x `FastMCP` examples.
- Tools return **pydantic models** (dicts produce no `structuredContent`).
- Core code (`src/taktloop/`, excluding tests) ≤ ~1,200 lines; no module over ~300 lines.
- One process, one SQLite file; default `.taktloop/factory.db`, gitignored.
- Default server `127.0.0.1:8765`, path `/mcp`. Default lease 30 minutes.
- Docs in English; product name always "taktloop".
- Timestamps: timezone-aware UTC, stored as ISO 8601 text.

## Review Focus

1. **Two sessions race to claim the same step** → exactly one wins; the other gets "step is already claimed". (Test in Task 3.)
2. **A session completes with an artifact path like `../secrets` or `/etc/passwd`, or a short SHA** → rejected with the reason, nothing changes, and the rejection shows up in history. (Task 3.)
3. **The core restarts while a step is claimed** → the claim and its token survive; the same token can still complete it. (Task 7.)
4. **`approve` is run while the task is not at a gate** (still in design, or already done) → a clear rejection, not a crash or a silent no-op. (Task 4.)
5. **The CLI is run while the core is down** → one line "taktloop core not reachable at http://127.0.0.1:8765/mcp — start it with `uv run taktloop serve`" and exit code 2, no traceback. (Task 6.)

---

## File Structure

| File | Responsibility |
| --- | --- |
| `pyproject.toml`, `uv.lock` | uv project, script entry `taktloop = "taktloop.cli:main"` |
| `src/taktloop/workflow.py` | parse + validate the `workflow:` block; pure |
| `src/taktloop/config.py` | read `factory.yaml` into `FactoryConfig` |
| `src/taktloop/store.py` | SQLite schema, transactions, row CRUD |
| `src/taktloop/models.py` | pydantic result/input models shared by service, server, CLI |
| `src/taktloop/service.py` | operations and rules, history, waiting-for, leases |
| `src/taktloop/mcp_server.py` | `MCPServer` tool surface, caller mapping, error mapping |
| `src/taktloop/cli.py` | `taktloop` command (argparse), rendering |
| `templates/mvp/factory.yaml` | the MVP workflow template (spec §6.2) |
| `templates/mvp/skills/taktloop/SKILL.md` | the bridge skill |
| `factory.yaml` | this repo's factory (copy of the template) |
| `tests/…` | one test module per source module, plus `tests/test_e2e.py` |

---

### Task 1: Project, workflow loader and config

**Files:**
- Create: `pyproject.toml`, `src/taktloop/__init__.py`, `src/taktloop/workflow.py`, `src/taktloop/config.py`
- Modify: `.gitignore` (add `.taktloop/`, `.venv/`, `__pycache__/`)
- Test: `tests/test_workflow.py`, `tests/test_config.py`

**Interfaces:**
- Produces:
  - `DONE = "done"`
  - `class WorkflowError(ValueError)`
  - `@dataclass(frozen=True) class Step: name: str; kind: Literal["agent", "human_gate"]; role: str; skill: str | None; instructions: str; required_artifacts: tuple[str, ...]; next: str | None; on_approve: str | None; on_reject: str | None`
  - `@dataclass(frozen=True) class Workflow: start: str; steps: Mapping[str, Step]` with `def step(self, name: str) -> Step`
  - `def parse_workflow(data: dict) -> Workflow`
  - `@dataclass(frozen=True) class FactoryConfig: factory: str; host: str; port: int; database: Path; workflow: Workflow; lease_minutes: int` with `url` property → `f"http://{host}:{port}/mcp"`
  - `def load_config(path: Path) -> FactoryConfig` — `database` resolved relative to the config file's directory; defaults host `127.0.0.1`, port `8765`, database `.taktloop/factory.db`, `lease_minutes` 30.

- [ ] **Step 1: Create the uv project**

Run: `uv init --package --name taktloop --build-backend hatch --no-readme .` (keep existing README/LICENSE), then `uv add "mcp>=2.3" pyyaml` and `uv add --dev pytest`. Set `requires-python = ">=3.12"` and `[project.scripts] taktloop = "taktloop.cli:main"` (create `cli.py` with a stub `def main(argv=None) -> int: return 0` so the entry point resolves).
Expected: `uv run python -c "import mcp.server.mcpserver, yaml"` exits 0.

- [ ] **Step 2: Write failing tests** in `tests/test_workflow.py` using the spec §6.2 YAML as fixture `MVP`:
  - `test_parses_mvp_workflow`: `wf.start == "design"`; `wf.step("design").required_artifacts == ("design_doc",)`; `wf.step("design_approval").kind == "human_gate"`, `.on_reject == "design"`; `wf.step("plan").next == DONE`.
  - `test_rejects_unknown_start`, `test_rejects_dangling_next` (`next: nowhere`), `test_rejects_unreachable_step` (extra step nobody points to), `test_rejects_unsupported_kind` (`kind: code` → message contains `"not supported yet"`), `test_agent_step_requires_skill_and_output`, `test_gate_requires_on_approve_and_on_reject`, `test_step_requires_role`, `test_step_named_done_is_reserved`. Each: `pytest.raises(WorkflowError, match=<step name>)`.
  - `tests/test_config.py::test_defaults_and_relative_db`: a `factory.yaml` with only `factory` + `workflow` in `tmp_path` → `cfg.port == 8765`, `cfg.database == tmp_path / ".taktloop/factory.db"`, `cfg.url == "http://127.0.0.1:8765/mcp"`.

- [ ] **Step 3: Run** `uv run pytest tests/test_workflow.py tests/test_config.py -q` — Expected: FAIL (import errors).

- [ ] **Step 4: Implement** `parse_workflow` and `load_config`. Reachability: walk from `start` over `next`/`on_approve`/`on_reject`.

- [ ] **Step 5: Run** the same command — Expected: all PASS.

- [ ] **Step 6: Commit** `git add pyproject.toml uv.lock .gitignore src tests && git commit -m "Add uv project, workflow loader and factory config"`

---

### Task 2: SQLite store

**Files:**
- Create: `src/taktloop/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Produces (rows are frozen dataclasses mirroring spec §6.1 columns, timestamps as `datetime`):
  - `TaskRow(id, title, request, status, created_by, created_at)`
  - `StepRunRow(id, task_id, step, round, status, claimed_by, claim_token, lease_expires_at, outcome, evidence: dict | None, opened_at, closed_at)`
  - `ArtifactRow(task_id, step_run_id, name, path, commit, branch)`
  - `EventRow(id, at, caller, operation, task_id, step_run_id, detail: dict, result, reason)`
  - `class SchemaMismatch(RuntimeError)`
  - `class Store: __init__(self, path: Path | str)` (`":memory:"` allowed; creates parent dir; `check_same_thread=False`; WAL; creates schema at `PRAGMA user_version = 1`; raises `SchemaMismatch` on another version) and `def transaction(self) -> ContextManager[Tx]` — holds a `threading.Lock`, runs `BEGIN IMMEDIATE`, commits on exit, rolls back on exception.
  - `class Tx` methods: `insert_task(title, request, created_by, at) -> int`, `get_task(id) -> TaskRow | None`, `list_tasks(statuses: Collection[str]) -> list[TaskRow]`, `set_task_status(id, status)`, `insert_step_run(task_id, step, round, at) -> int`, `get_step_run(id) -> StepRunRow | None`, `current_step_run(task_id) -> StepRunRow | None` (latest with status `open`/`claimed`), `last_step_run(task_id, step) -> StepRunRow | None`, `step_runs_with_status(statuses) -> list[StepRunRow]`, `update_step_run(id, **fields)`, `insert_artifact(row: ArtifactRow)`, `list_artifacts(task_id) -> list[ArtifactRow]`, `insert_event(at, caller, operation, task_id, step_run_id, detail, result, reason) -> int`, `list_events(task_id) -> list[EventRow]` (ordered by id).

- [ ] **Step 1: Write failing tests:**
  - `test_task_and_step_run_roundtrip`: insert task + step run, read back equal values; `current_step_run` returns it; after `update_step_run(id, status="completed")` returns `None`.
  - `test_rollback_on_exception`: insert inside a `transaction()` that raises → `get_task` in a new transaction is `None`.
  - `test_persists_across_instances` (`tmp_path/"f.db"`): write, new `Store`, read back.
  - `test_schema_version_mismatch`: set `PRAGMA user_version = 99` via raw sqlite → `Store(path)` raises `SchemaMismatch`.
  - `test_events_ordered_and_detail_json`: two events, `detail={"a": [1]}` round-trips.
- [ ] **Step 2: Run** `uv run pytest tests/test_store.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** `store.py` (plain SQL, no ORM; JSON columns via `json.dumps`).
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "Add SQLite store"` (with the two files).

---

### Task 3: Service — tasks and the step lifecycle

**Files:**
- Create: `src/taktloop/models.py`, `src/taktloop/service.py`
- Test: `tests/test_service_steps.py`

**Interfaces:**
- Consumes: `Store`, `Tx`, rows (Task 2); `Workflow`, `Step`, `DONE` (Task 1).
- Produces (`models.py`, pydantic `BaseModel`s):
  - `ArtifactIn(name: str, path: str, commit: str, branch: str)`
  - `Artifact(ArtifactIn)` + `step: str`, `round: int`
  - `TaskSummary(id: int, title: str, status: str, current_step: str | None, step_id: int | None, waiting_for: str)`
  - `OpenStep(step_id: int, task_id: int, task_title: str, step: str, skill: str, round: int)`
  - `Claim(step_id: int, claim_token: str, lease_expires_at: datetime)`
  - `StepContext(step_id, task_id, task_title, request, step, skill, instructions, required_artifacts: list[str], round: int, rejection_reason: str | None, artifacts: list[Artifact])`
- Produces (`service.py`):
  - `@dataclass(frozen=True) class Caller: subject: str; roles: frozenset[str]; executor: str | None = None` with `label` → `"local (claude-code)"` / `"local"`
  - `class Rejected(Exception)` with `.reason: str`
  - `class Service: __init__(self, store: Store, workflow: Workflow, lease: timedelta = timedelta(minutes=30), clock: Callable[[], datetime] = <utc now>)`
  - `create_task(caller, title: str, request: str = "") -> TaskSummary`
  - `list_open_steps(caller) -> list[OpenStep]` (agent steps only)
  - `claim_step(caller, step_id: int) -> Claim`
  - `get_step_context(caller, step_id: int) -> StepContext`
  - `heartbeat(caller, step_id: int, claim_token: str) -> Claim`
  - `complete_step(caller, step_id: int, claim_token: str, outcome: str, artifacts: list[ArtifactIn]) -> TaskSummary`
  - `release_step(caller, step_id: int, reason: str, claim_token: str | None = None, force: bool = False) -> TaskSummary`
  - `waiting_for(...)` is private; strings exactly as spec §6.5: `"waiting for a planner to pick up design"`, `"design in progress by local (claude-code), lease until 14:30 UTC"`, `"waiting for approver decision at design_approval"`, `"done"`.

Rules every operation follows: one `store.transaction()` per operation; on success write one `events` row (`operation` = method name, `result="ok"`, `detail` = arguments minus `claim_token`); on `Rejected`, roll back, then write a `result="rejected"` event with the reason in a second transaction, and re-raise. **Lazy lease expiry:** any operation reading a step run whose `status == "claimed"` and `lease_expires_at <= clock()` first sets it `open`, clears token and holder, and writes a `lease_expired` event (once).

- [ ] **Step 1: Write failing tests** (fixture: `Service(Store(":memory:"), parse_workflow(MVP), clock=fake)` where `fake` is a mutable clock; `ME = Caller("local", frozenset({"*"}), "claude-code")`):
  - `test_create_task_opens_start_step`: summary `current_step == "design"`, `waiting_for == "waiting for a planner to pick up design"`; `list_open_steps` has one entry with `skill == "brainstorming"`.
  - `test_create_task_rejects_empty_title` (`"  "` → `Rejected`).
  - `test_claim_returns_token_and_lease`: `lease_expires_at == fake() + 30 min`; step no longer in `list_open_steps`.
  - `test_second_claim_rejected`: second `claim_step` → `Rejected("step is already claimed")`.
  - `test_wrong_token_rejected_for_heartbeat_complete_release` (three asserts).
  - `test_heartbeat_extends_lease`.
  - `test_complete_requires_all_artifacts`: `artifacts=[]` → `Rejected` mentioning `design_doc`.
  - `test_complete_rejects_bad_paths_and_shas`: parametrize over `"../x.md"`, `"/etc/passwd"`, `"a/../../b"` (path) and `"abc123"`, `"Z"*40` (commit) → `Rejected`; after each, `get_step_run` unchanged and the last event has `result == "rejected"`.
  - `test_complete_opens_next_step`: valid artifact (40-hex SHA) → summary `current_step == "design_approval"`, `waiting_for == "waiting for approver decision at design_approval"`.
  - `test_lease_expires_lazily`: advance clock 31 min → `list_open_steps` shows the step again; old token rejected for `complete_step`; exactly one `lease_expired` event even after two reads.
  - `test_release_returns_step_and_force_needs_no_token`.
  - `test_design_step_context`: context of the claimed design step has the task's `request`, the YAML `instructions`, `skill == "brainstorming"`, `required_artifacts == ["design_doc"]`, `round == 1`, `rejection_reason is None`, `artifacts == []` (artifacts from earlier steps are Task 4).
  - `test_concurrent_claims_one_winner`: 8 threads `claim_step` the same id on a `tmp_path` file store → exactly one `Claim`, seven `Rejected`.
- [ ] **Step 2: Run** `uv run pytest tests/test_service_steps.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** `models.py` and these `Service` methods. Path check: `PurePosixPath(p)` not absolute, no `..` part, non-empty; commit `re.fullmatch(r"[0-9a-f]{40}", c)`; claim token `secrets.token_hex(16)`.
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "Add service: tasks and step lifecycle"`.

---

### Task 4: Service — gates, task views and history

**Files:**
- Modify: `src/taktloop/models.py`, `src/taktloop/service.py`
- Test: `tests/test_service_gates.py`

**Interfaces:**
- Consumes: everything from Task 3.
- Produces:
  - `Event(at: datetime, caller: str, operation: str, step: str | None, result: str, reason: str | None, detail: dict)`
  - `TaskDetail(TaskSummary)` + `request: str`, `artifacts: list[Artifact]`, `history: list[Event]`
  - `Service.decide_gate(caller, task_id: int, gate: str | None, decision: Literal["approve", "reject"], reason: str | None = None) -> TaskSummary` — `gate=None` means "the task's current gate"; `approve` opens `on_approve` (round = previous rounds of that step + 1); `reject` requires `reason`, opens `on_reject` as a new round; the gate's event `detail` lists the artifacts the decision applies to (all artifacts of the current rounds so far).
  - `Service.get_task(caller, task_id: int) -> TaskDetail`
  - `Service.list_tasks(caller, include_closed: bool = False) -> list[TaskSummary]`
  - `StepContext.rejection_reason` is filled for a reopened round from the latest `reject` event; `StepContext.artifacts` lists artifacts from earlier steps' **latest completed** rounds.
  - Reaching `DONE` sets task `status = "done"` and `waiting_for == "done"`.

- [ ] **Step 1: Write failing tests** (helper `complete_design(svc, task_id, sha)` claims and completes design):
  - `test_approve_opens_plan_and_context_has_design_doc`: after approve, `list_open_steps()[0].step == "plan"`; its context `artifacts[0].name == "design_doc"`, `.commit == sha`.
  - `test_reject_reopens_design_round_2_with_reason`: `reject` with `reason="needs CLI flags"` → open step `design`, `round == 2`; context `rejection_reason == "needs CLI flags"`.
  - `test_reject_requires_reason`.
  - `test_gate_rejected_when_not_at_gate`: during design, and after done → `Rejected` containing `"not at a gate"`; history records both rejections.
  - `test_named_gate_mismatch`: `gate="other"` → `Rejected`.
  - `test_full_flow_reaches_done`: design → approve → plan complete with `plan` artifact → `get_task` `status == "done"`, two artifacts, history operations in order `["create_task", "claim_step", "complete_step", "decide_gate", "claim_step", "complete_step"]` (filter `result == "ok"`; `get_step_context` and reads are **not** recorded).
  - `test_approve_event_pins_artifacts`: the `decide_gate` event `detail["artifacts"]` contains the design doc's `path` and `commit`, so round 1 rejected vs round 2 approved are distinguishable.
  - `test_list_tasks_hides_done_by_default`.
- [ ] **Step 2: Run** `uv run pytest tests/test_service_gates.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement.** Only state-changing operations write events (reads don't).
- [ ] **Step 4: Run** `uv run pytest -q` — Expected: all PASS.
- [ ] **Step 5: Commit** `git commit -m "Add service: gates, task views and history"`.

---

### Task 5: MCP server

**Files:**
- Create: `src/taktloop/mcp_server.py`
- Test: `tests/test_mcp_server.py`

**Interfaces:**
- Consumes: `Service`, `Caller`, `Rejected`, all models.
- Produces:
  - `def build_server(service: Service) -> MCPServer` — `MCPServer("taktloop", instructions=<one paragraph: what the core is, and to use the taktloop skill>)`; registers tools named exactly as spec §6.3: `create_task`, `list_tasks`, `get_task`, `list_open_steps`, `claim_step`, `get_step_context`, `heartbeat`, `complete_step`, `release_step`, `decide_gate`. Parameters as in Tasks 3–4 (minus `caller`); list results wrapped in a model (`TaskList(tasks: list[TaskSummary])`, `OpenStepList(steps: list[OpenStep])`) so they're structured.
  - `def caller_from(ctx: Context) -> Caller` → `Caller("local", frozenset({"*"}), executor=ctx.session.client_params.client_info.name)` (fall back to `None` if missing). **This is M2's seam.**
  - Every tool catches `Rejected` and raises `ToolError(reason)`.

- [ ] **Step 1: Write failing tests** with the in-memory client: `async with Client(build_server(svc), client_info=Implementation(name="claude-code", version="0")) as c:`
  - `test_lists_exactly_the_m1_tools`: `{t.name for t in (await c.list_tools()).tools}` equals the ten names.
  - `test_create_and_claim_structured`: `r = await c.call_tool("create_task", {"title": "T"})`; `r.structured_content["current_step"] == "design"`; claim returns `claim_token` (32 hex chars).
  - `test_rejection_is_tool_error_with_reason`: second claim → `r.is_error` and `"already claimed" in r.content[0].text`.
  - `test_executor_name_reaches_waiting_for`: after claim, `get_task` `waiting_for` contains `"local (claude-code)"`.
- [ ] **Step 2: Run** `uv run pytest tests/test_mcp_server.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** (sync tool functions are fine; the SDK runs them in worker threads, and `Store`'s lock serialises them).
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "Add MCP tool surface"`.

---

### Task 6: CLI

**Files:**
- Modify: `src/taktloop/cli.py`
- Test: `tests/test_cli.py`

**Interfaces:**
- Consumes: `load_config`, `FactoryConfig.url` (Task 1); `build_server` (Task 5); `Store`, `Service` (for `serve` only).
- Produces:
  - `def main(argv: list[str] | None = None) -> int`
  - Global options: `--config PATH` (default `./factory.yaml`), `--url URL` (overrides config), `--json`.
  - Commands, exactly: `serve`; `task create TITLE [--request TEXT | --request -]`; `task list [--all]`; `task show ID`; `approve TASK [--gate NAME]`; `reject TASK --reason TEXT [--gate NAME]`; `release STEP_ID --force --reason TEXT`; `steps`; `install-skill [--dest DIR]` (default `.claude/skills/taktloop`).
  - `serve`: `build_server(Service(Store(cfg.database), cfg.workflow, timedelta(minutes=cfg.lease_minutes))).run("streamable-http", host=cfg.host, port=cfg.port)`; prints one line `taktloop core for factory '<name>' on <url>`.
  - Client commands use `Client(url, client_info=Implementation(name="taktloop-cli", version=<package version>))`. Tool error → print `rejected: <reason>` to stderr, exit 1. Connection failure → exactly `taktloop core not reachable at <url> — start it with \`uv run taktloop serve\``, exit 2.
  - `install-skill` copies `templates/mvp/skills/taktloop/` found at `Path(__file__).resolve().parents[2] / "templates"` (M1 runs from a checkout via `uv run`; no wheel packaging); prints the destination; overwrites existing files.
  - Rendering, pure functions `render_task_detail(d: dict) -> str`, `render_task_list(d: dict) -> str`, `render_steps(d: dict) -> str`: `task show` prints `#<id> <title> [<status>]`, then `step: <current_step>  (step id <step_id>)`, then `waiting for: …`, then an artifacts table (`name  path@commit[:12]  branch`), then a history table (`time  caller  operation  step  result/reason`). `--json` prints the structured content with `json.dumps(indent=2)`.

- [ ] **Step 1: Write failing tests:**
  - `test_render_task_detail_lines`: from a hand-built dict, output contains `"#1 Add a --version flag [active]"`, `"waiting for: waiting for approver decision at design_approval"` and the artifact line with a 12-char SHA.
  - `test_core_down_message_and_exit_code`: `main(["--url", "http://127.0.0.1:1/mcp", "task", "list"])` returns `2`; `capsys` stderr equals the exact message.
  - `test_install_skill_copies_bridge_skill`: `main(["install-skill", "--dest", str(tmp_path/"s")])` → `(tmp_path/"s"/"SKILL.md").exists()` (create a placeholder `templates/mvp/skills/taktloop/SKILL.md` in this task; Task 7 writes its content).
  - `test_rejection_exit_code`: monkeypatch `cli._call_tool` to raise `ToolRejected("x")`; `main(["approve", "1"])` returns `1` and stderr is `"rejected: x\n"`. (The real server path is covered by Task 7's e2e test.)
- [ ] **Step 2: Run** `uv run pytest tests/test_cli.py -q` — Expected: FAIL.
- [ ] **Step 3: Implement** with `argparse` subparsers; one private `async def _call_tool(url, name, args) -> dict` used by all client commands (raises `ToolRejected` on `is_error`, `CoreUnreachable` on `httpx`/connection errors — catch the transport's connect error class and `OSError`).
- [ ] **Step 4: Run** — Expected: PASS.
- [ ] **Step 5: Commit** `git commit -m "Add taktloop CLI"`.

---

### Task 7: Template, bridge skill, factory and end-to-end test

**Files:**
- Create: `templates/mvp/factory.yaml` (spec §6.2 verbatim), `factory.yaml` (copy), `templates/mvp/skills/taktloop/SKILL.md`
- Modify: `README.md` (an "M1: try it" section)
- Test: `tests/test_e2e.py`, `tests/test_templates.py`

**Interfaces:**
- Consumes: the CLI (Task 6), all tools (Task 5).

- [ ] **Step 1: Write failing tests:**
  - `tests/test_templates.py::test_template_and_repo_factory_load`: `load_config` on both files succeeds and both workflows are equal.
  - `test_bridge_skill_frontmatter`: `SKILL.md` starts with YAML frontmatter containing `name: taktloop` and a `description`; body mentions every tool name used in the lifecycle (`list_open_steps`, `claim_step`, `get_step_context`, `heartbeat`, `complete_step`, `release_step`) and the phrase `claim_token`.
  - `tests/test_e2e.py::test_task_survives_restart` (fixture writes a `factory.yaml` with a free port and `tmp_path` database, starts `uv run taktloop --config … serve` as a subprocess, waits until `task list` exits 0, kills it on teardown): run the CLI / an MCP `Client` against it to create a task, claim design (keep the token), **kill and restart the server**, complete design with that token, `approve`, claim + complete plan, then `task show 1 --json` → `status == "done"` and two artifacts. Also: a second `Client` calling `complete_step` with a wrong token → `is_error`.
- [ ] **Step 2: Run** `uv run pytest tests/test_templates.py tests/test_e2e.py -q` — Expected: FAIL.
- [ ] **Step 3: Write the files.** `SKILL.md` follows spec §6.7 points 1–7, generic (no superpowers names except as an *example* of chaining to ignore). It also states: keep the `claim_token` from `claim_step` and pass it to `heartbeat`/`complete_step`/`release_step`; commit the artifact first and report `path`, full `commit` SHA (`git rev-parse HEAD`) and `branch`; read earlier artifacts with `git show <commit>:<path>`; for gates, tell the user `uv run taktloop approve <task>`. README section: `uv sync`; `uv run taktloop serve`; `claude mcp add --transport http taktloop http://127.0.0.1:8765/mcp`; `uv run taktloop install-skill`; `uv run taktloop task create "…"`; in Claude Code say "work on taktloop"; `uv run taktloop task show 1`; `uv run taktloop approve 1`.
- [ ] **Step 4: Run** `uv run pytest -q` — Expected: all PASS. Then `find src -name '*.py' | xargs wc -l | tail -1` — Expected: ≤ ~1,200; no single file > ~300.
- [ ] **Step 5: Commit** `git commit -m "Add MVP template, bridge skill and end-to-end test"`.

---

### Task 8: The M1 demo with @Tobias (manual acceptance)

Not automatable: it needs a person and real Claude Code sessions. Run spec §7 steps 1–6 on this repo with @Tobias:

- [ ] **Step 1:** Core in one terminal (`uv run taktloop serve`); MCP registered; skill installed.
- [ ] **Step 2:** `uv run taktloop task create "Add a --version flag to the CLI"`; session A: "work on taktloop" → design via brainstorming; confirm the session **stops** after `complete_step` and does not start writing-plans.
- [ ] **Step 3:** close session A; `task show 1` shows the gate wait and full history; `reject 1 --reason …`; revise in a session; restart the core; `approve 1`.
- [ ] **Step 4:** new session B: "work on taktloop" → plan from the pinned design doc; stops after completing; `task show 1` → done.
- [ ] **Step 5:** Note every place the bridge skill was ignored or unclear; feed that into the skill wording and into the M2 design round. Commit any skill fixes: `git commit -m "Tune bridge skill after M1 demo"`.

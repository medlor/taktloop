# taktloop MVP: milestones and M1 design

Status: draft · Nov 1, 2026 · @Tobias

> Calls marked **[builder call]** were made while writing this design, not agreed with @Tobias. Check them while reading.

## 1. Intent

The PRD (`docs/prd.md` v4), ADRs 0001–0004, `rbac.md` and `mvp-workflow.md` describe the MVP. This document cuts the MVP into **milestones**, each one a **working prototype run against this repository** (taktloop building itself). The point is early feedback from @Tobias: every milestone is something he can use end to end and react to, and each one moves towards the full MVP. A milestone is never "component X implemented on its own".

The full design of M1 is below. M2–M4 are sketched only; each gets its own design round once the milestone before it has been tried.

## 2. Milestones

| # | Name | What @Tobias can do at the end | PRD problem it makes tangible |
| --- | --- | --- | --- |
| **M1** | **A task survives sessions** | create a task from the CLI; a Claude Code session with the bridge skill pulls the design step and runs brainstorming with him; he approves the design gate from the CLI; a *new* session pulls the plan step and writes the plan; at any time the CLI shows state, history and what the task is waiting for | 2.4 single source of truth |
| M2 | Roles and separation of duties | the dev issuer issues tokens per role identity; every core call is authorized and audited; plan review runs as a second agent; the core refuses self-review and self-approval | 2.5 unclear authority |
| M3 | Plan to merge, with a hard gate | workspace, build, review, verify, integrate steps; "CI green" checked by the core against real GitHub Actions via GitHub's MCP server; first code step; first pushed step (headless reviewer); lease expiry and stall detection | 2.5 hard gates, 2.4 across a full lifecycle |
| M4 | Full MVP | ticket stand-in with out-of-sync handling; triage routes; startup availability check; binding-switch and breakout demos; user docs and < 30 min setup; lightweight-budget check | 2.1, 2.6, all success criteria |

The order puts the core's reason to exist (2.4) first and authority (2.5) right after it.

## 3. Approaches considered (stack)

| Option | Runtime deps | Trade-off |
| --- | --- | --- |
| **A. Python 3.12+ with uv (chosen)** | `mcp` ≥ 2.3 (official SDK: `MCPServer`, the class formerly called FastMCP; streamable HTTP; OAuth resource-server helpers for M2), `pyyaml`; `sqlite3` from stdlib; `pydantic`, `uvicorn` and `pyjwt[crypto]` arrive via `mcp`, so M2 needs no new direct dependency | @Tobias's default stack; concise code keeps the core well inside the ~3,000-line budget; SDK covers M1 and M2 auth |
| B. TypeScript on Node 22+ | `@modelcontextprotocol/sdk`, `zod`, `yaml`, `node:sqlite`, later `jose` | MCP reference SDK, but not the default stack and more type ceremony |
| C. Go | official go-sdk, `modernc.org/sqlite` | single binary, but verbose (tight line budget) and the youngest SDK |

**Chosen: A**, by @Tobias. Tooling rule from @Tobias: **always astral uv**. The core is a uv project (`pyproject.toml`, `uv.lock`, `uv run …`); standalone scripts use PEP 723 inline dependencies.

## 4. M1 goals, non-goals, anti-goals

**Goals**

- One task moves **intake → design → design approval → plan → done** across at least two separate harness sessions and a human wait of any length, with nothing held in any session.
- At any moment, `taktloop task show <id>` answers: which step, who holds it, what it's waiting for, and the full history.
- The bridge skill makes a harness stop at the step boundary, although brainstorming would chain straight into writing-plans.
- The workflow is data (YAML), not code: changing the step order needs no core change.

**Non-goals for M1** (they come in later milestones)

- Authentication, roles, separation of duties, audit by identity (M2). M1 runs with one trusted local identity.
- Plan review by a second agent (M2), build/review/verify/integrate (M3), code steps, pushed steps, the orchestrator, gate checks via external MCP servers (M3), the ticket stand-in, triage, the availability check (M4).
- Lease *expiry enforcement* with stall escalation (M3). M1 records leases and expires them lazily (see §6.4), nothing more.

**Anti-goals** (failure even if it technically works)

- State or the "next step" decision living in the harness or the skill instead of the core.
- A code shape that has to be rewritten in M2 to add tokens, or in M3 to add gate checks and code steps. The seams must be there from the start (§5).
- Growing a general workflow engine: no conditions, loops or expressions in the workflow YAML beyond what §6.2 needs.
- Exceeding the lightweight budget: M1 aims for **≤ ~1,200 lines** of core code (excluding tests) and **2 direct runtime dependencies**.

## 5. M1 architecture

One process, one SQLite file.

```
Claude Code session ──(MCP, streamable HTTP)──┐
Claude Code session ──(MCP, streamable HTTP)──┤
taktloop CLI ─────────(MCP client)────────────┤
                                              ▼
                              taktloop core (uv run taktloop serve)
                              ┌────────────────────────────────┐
                              │ mcp_server  – MCP tool surface │
                              │ service     – operations, rules│
                              │ workflow    – load/validate YAML│
                              │ store       – SQLite access    │
                              └────────────────────────────────┘
                                              │
                                        factory.db (SQLite)
```

Module boundaries (dependency direction top to bottom; nothing points upward):

| Module | Responsibility | Knows about |
| --- | --- | --- |
| `cli.py` | `taktloop` command; `serve` starts the server, every other command is an MCP client call to a running core | the core's MCP tools only (never imports `service`/`store`) |
| `mcp_server.py` | `MCPServer` app (`from mcp.server.mcpserver import MCPServer`; mcp 2.x API, not the 1.x `FastMCP` examples found online); one thin function per tool; maps errors to MCP tool errors; returns structured results | `service` |
| `service.py` | all operations and rules (§6.3); the single place where state changes; takes a `caller` argument on every operation | `workflow`, `store` |
| `workflow.py` | load and validate `factory.yaml`; answer "what opens after step X with outcome Y" | nothing (pure) |
| `store.py` | SQLite schema and queries, transactions | stdlib `sqlite3` |
| `config.py` | locate and read `factory.yaml`, host/port/db path | `workflow` |

**Seams for later milestones** (present in M1, trivially filled):

- **Caller identity:** every `service` operation takes a `Caller(subject, roles, executor)`. In M1 `mcp_server` always passes `Caller("local", roles={"*"}, executor=<the MCP client's clientInfo name>)`. M2 replaces this one function with token validation (subject from the token).
- **Claim ownership without identities** **[builder call]**: `claim_step` returns a random **claim token**; `heartbeat`, `complete_step` and `release_step` require it. Session B therefore can't heartbeat, complete or release a step session A holds. (An earlier draft used the `mcp-session-id` header, but the stateless MCP protocol `2026-07-28`, which `mcp` 2.3 uses by default, sends none.) `task show` names the holder as `local (claude-code)`, from the client's name. In M2 the token's subject is checked as well.
- **Stuck claims:** if a session crashes, its claim blocks the step until the lease expires (§6.4). `taktloop release <step_id> --force --reason TEXT` lets the human free it immediately (recorded as a forced release) **[builder call]**.
- **History is the audit log:** every operation that changes state writes one `events` row (§6.1) with caller, operation, task, step and outcome. M2 adds denied calls and roles; the table already has the columns.
- **Step `kind`:** the workflow loader accepts `agent` and `human_gate` in M1 and rejects other kinds with a clear "not supported yet" error. M3 adds `code` and gate `check`.

**The CLI talks MCP, not a separate API** **[builder call]**: one interface for harnesses and humans, no second surface to keep consistent. The CLI therefore needs a running core (except `serve` and `install-skill`); with one process that's the normal state. In M2 the CLI authenticates to the core like any MCP client, with a bearer token from the dev issuer. The PRD's "plain API" (an MVP interface, PRD §3a) is **moved to M4, not dropped**: it's a thin HTTP/JSON route over the same `service` operations, added when a non-MCP client (webhook, script) needs it.

**Transport:** streamable HTTP on `127.0.0.1:8765` by default **[builder call]**, configurable in `factory.yaml`. Sessions register it as an HTTP MCP server (`claude mcp add --transport http taktloop http://127.0.0.1:8765/mcp`).

## 6. M1 behaviour

### 6.1 Data model (SQLite)

| Table | Columns (essentials) |
| --- | --- |
| `tasks` | `id` (int), `title`, `request` (text), `status` (`active` / `done` / `cancelled`), `created_by`, `created_at` |
| `step_runs` | `id` (int, the `step_id` used in tools), `task_id`, `step` (name from YAML), `round` (1, 2, … when a step reopens), `status` (`open` / `claimed` / `completed` / `released` / `superseded`), `claimed_by`, `claim_token`, `lease_expires_at`, `outcome`, `evidence` (JSON), `opened_at`, `closed_at` |
| `artifacts` | `task_id`, `step_run_id`, `name` (for example `design_doc`), `path` (repo-relative), `commit` (full 40-char SHA), `branch` |
| `events` | `id`, `at`, `caller`, `operation`, `task_id`, `step_run_id`, `detail` (JSON), `result` (`ok` / `rejected` + reason) |

At most one non-closed step run exists per task in M1 (the workflow is linear with one back edge). Schema created on first start; no migrations framework in M1 **[builder call]**: a `schema_version` pragma and a hard error on mismatch.

### 6.2 Workflow file

`factory.yaml` at the repo root (path overridable with `--config`). The MVP template ships at `templates/mvp/factory.yaml`; this repo's `factory.yaml` is a copy of it (PRD §3: a factory gets a copy).

```yaml
factory: taktloop
server: {host: 127.0.0.1, port: 8765}
database: .taktloop/factory.db        # gitignored
workflow:
  start: design
  steps:
    design:
      kind: agent
      role: planner
      skill: brainstorming
      instructions: >
        Run brainstorming with the requester until the design doc is written
        and the requester is happy with it. Do not invoke writing-plans.
      output:
        artifacts: [design_doc]        # required artifact names
      next: design_approval
    design_approval:
      kind: human_gate
      role: approver
      on_approve: plan
      on_reject: design                # reopens design as round n+1, with the reason
    plan:
      kind: agent
      role: planner
      skill: writing-plans
      instructions: >
        Write the plan from the approved design doc. Do not ask for an
        execution mode and do not start executing.
      output:
        artifacts: [plan]
      next: done                       # reserved name: the task is finished
```

Validation at load (start fails with a clear message): `start` exists; every `next`/`on_approve`/`on_reject` names a step or `done`; every step is reachable; `kind` is supported; `agent` steps have `skill` and `output`; `role` is present (recorded but not enforced in M1).

### 6.3 Core MCP tools

All tools return structured content (pydantic models → `structuredContent`). Errors are MCP tool errors with a short reason, and they are recorded in `events` too.

| Tool | Does | Rejects when |
| --- | --- | --- |
| `create_task(title, request)` | creates the task and opens the `start` step | empty title |
| `list_tasks(status?)` | tasks with their current step and waiting-for line | — |
| `get_task(task_id)` | task, current step run, artifacts, full event history, **waiting-for** (§6.5) | unknown task |
| `list_open_steps()` | open `agent` step runs (id, task, step, skill, summary). Human gates are not listed here | — |
| `claim_step(step_id)` | marks it claimed by the caller with a lease (default 30 min **[builder call]**); returns `step_id`, `claim_token`, `lease_expires_at` | not open (already claimed with an unexpired lease, completed, superseded) |
| `get_step_context(step_id)` | task title and request; step name, skill, instructions, required output; artifacts of earlier steps; the latest rejection reason if this is a reopened round | unknown step |
| `heartbeat(step_id, claim_token)` | extends the lease | wrong or stale claim token |
| `complete_step(step_id, claim_token, outcome, artifacts)` | `artifacts` is a list of `{name, path, commit, branch}`; validates they cover every required name; records them; closes the run; opens the next step (or finishes the task) | wrong or stale claim token; missing artifact; path not relative or contains `..`; `commit` not a 40-char hex SHA |
| `release_step(step_id, reason, claim_token?, force?)` | returns it to `open` | wrong or stale claim token (unless `force`) |
| `decide_gate(task_id, gate, decision, reason?)` | `approve` → opens `on_approve`; `reject` (reason required) → reopens `on_reject` as a new round | task isn't at that gate |

`outcome` is `completed` for agent steps in M1 (the field exists so M2's plan review can return `issues`).

**Artifacts are pinned to a commit** **[builder call]**. The design is revised in place across rounds, so a bare path can't show which version was rejected and which was approved. With `path@commit`, the history records exactly what each gate decision was about, and the plan step reads the approved version (`git show <commit>:<path>`) even from another branch or worktree. The core validates the *shape* only and doesn't open the repository: it may run outside the checkout, and verifying evidence through a tool is M3's gate-check mechanism. `get_step_context` returns earlier artifacts with their pinned refs; `decide_gate` records the artifacts it applies to in its event.

### 6.4 Leases

A claim sets `lease_expires_at`. M1 enforces expiry **lazily**: any read or claim treats an expired claimed run as `open` again (and writes a `lease_expired` event the first time it notices). No background timer in M1.

### 6.5 Waiting-for

Derived, never stored: open agent step → "waiting for a planner to pick up *design*"; claimed → "*design* in progress by `local`, lease until 14:30"; human gate → "waiting for approver decision at *design_approval*"; done → "done".

### 6.6 CLI

`uv run taktloop …` (installed as a uv project script):

| Command | Maps to |
| --- | --- |
| `serve [--config factory.yaml]` | starts the core |
| `task create "<title>" [--request TEXT or -]` | `create_task` |
| `task list [--all]` | `list_tasks` |
| `task show <id>` | `get_task`, rendered as state + waiting-for + history table |
| `approve <task> [--gate NAME]` / `reject <task> --reason TEXT` | `decide_gate` (gate defaults to the task's current gate) |
| `release <step_id> --force --reason TEXT` | `release_step` with `force=true` (frees a stuck claim; M1 has no roles, M2 limits it to the factory owner) |
| `install-skill [--dest DIR]` | copies the bridge skill (default `.claude/skills/taktloop/`) |
| `steps` | `list_open_steps` (for debugging) |

Output is plain text tables; `--json` prints the structured result **[builder call]**. No colour or TUI dependency.

### 6.7 Bridge skill

`templates/mvp/skills/taktloop/SKILL.md`, generic (knows the core tools and the lifecycle, nothing about superpowers). Because `.claude/` is gitignored in this repo, `taktloop` ships the skill in `templates/` and the setup step copies it to `.claude/skills/taktloop/` **[builder call]**; a `taktloop install-skill [--dest DIR]` CLI command does the copy.

Its instructions:

1. On "work on taktloop" (or on start, when headless): call `list_open_steps`; offer the steps to the user, or pick one when headless.
2. `claim_step`, then `get_step_context`.
3. Run the step **with the skill the context names**, passing the step's instructions and the earlier artifacts.
4. **Step boundary takes precedence over any skill's chaining.** When the required output exists, call `complete_step` with the artifact refs and **stop**. Don't invoke the next skill, even if the running skill says to (brainstorming → writing-plans; writing-plans → execution choice).
5. In-chat approval checkpoints that a skill asks for (brainstorming's "approve the design doc") are still held with the user in the session, but the *binding* approval is the core's gate: tell the user to run `taktloop approve <task>` (or approve it later from anywhere).
6. During long conversations, `heartbeat` about every 15 minutes or before the lease expires. If the user stops, `release_step` with a reason.
7. On failure, `release_step` with the reason; never fake a completion.

Before `complete_step`, the agent **commits** the design doc or plan on the current branch (the skills do this already) and reports path, commit SHA and branch. To read an earlier artifact, it uses `git show <commit>:<path>` rather than the working tree.

## 7. M1 acceptance (the demo)

M1 is done when this works on this repo, with the core running in one terminal:

1. `taktloop task create "Add a --version flag to the CLI"` → task 1, waiting for a planner.
2. Claude Code session A (bridge skill installed, core MCP registered): "work on taktloop" → claims *design* of task 1, runs brainstorming, writes `docs/superpowers/specs/…-design.md`, completes the step, and **stops** without invoking writing-plans.
3. Session A is closed. `taktloop task show 1` → waiting for approver at *design_approval*, history lists every event.
4. `taktloop reject 1 --reason "…"` → design reopens as round 2; a session sees the reason in the context and revises. Then `taktloop approve 1`.
5. Restart the core in between (state survives). A second session trying to complete or release a step held by another session (without its claim token) is rejected.
6. New session B: "work on taktloop" → claims *plan*, gets the design doc ref from context, writes the plan, completes, stops. `task show 1` → done, with design doc and plan artifacts.

Plus automated tests (pytest, run with `uv run pytest`): workflow validation, every tool's happy path and rejections, lazy lease expiry, reject/reopen rounds, restart persistence, and an MCP round trip through the SDK's in-memory client.

## 8. Constraints

- Python ≥ 3.12, uv for everything (project, lockfile, scripts with inline deps).
- Direct runtime dependencies in M1: `mcp` (≥ 2.3), `pyyaml`. Dev only: `pytest` (and whatever the SDK's test client needs).
- One process, one SQLite file in `.taktloop/` (gitignored). Concurrent tool calls must be safe: claims use `BEGIN IMMEDIATE` transactions.
- Code layout `src/taktloop/`, tests in `tests/`. Small modules, no module over ~300 lines.
- Docs in English. Product name always "taktloop".

## 9. Left to the builder

Exact pydantic model names and fields beyond those above; SQL details and indexes; CLI rendering; how the in-memory MCP test fixture is wired; the bridge skill's exact wording (within §6.7); README section for M1 setup.

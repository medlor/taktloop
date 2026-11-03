# taktloop M4: the full MVP — tickets, hooks, triage, live workflow changes, setup

Status: approved · Nov 3, 2026 · @Tobias

> Calls marked **[builder call]** were made while writing this design, not agreed with @Tobias. Check them while reading.

Builds on the M1, M2, M3a and M3b designs (`docs/superpowers/specs/2026-11-0{1,2,3}-…`), all implemented and on `main`, and on ADRs 0001–0004. Where this document is silent, those still hold.

## 1. Intent

After M3b a task goes from idea to merged code, partly on its own. What's still missing for the MVP (PRD §5, the milestone table in the M1 design):

- **Tickets** (PRD 2.2, 3a): people who live in a tracker see where each task stands, and taktloop notices when someone closes or deletes the ticket of an active task behind its back.
- **Triage** (mvp-workflow §2): not every task needs the full design → plan → build path.
- **Breakout** (PRD 2.6, use case D): a factory owner changes the workflow **while the factory runs**, with instant feedback, without touching the core's code.
- **A startup availability check** (ADR 0002): a factory never runs against an MCP server or tool that isn't there.
- **Setup** (PRD lightweight budget): a factory set up in under 30 minutes from short user docs, already split the way the later central service will be: taktloop and the orchestrator run **as a service**, teams only connect their product repos.
- **The plain HTTP API** that M1 moved to M4.

M4 is **one milestone** (decided with @Tobias, no split). As before it is a prototype @Tobias tries end to end, with **taktloop building itself** (§14).

## 2. Goals, non-goals, anti-goals

**Goals**

- A **ticket stand-in**: a small local MCP server, `taktloop tickets`, **its own process** with its own port and SQLite file (decided with @Tobias, approach B in §3). Jira is the reference for its shape (decided with @Tobias).
- **The task comes first** (decided with @Tobias): a code step opens the ticket when the task starts. Ticket-first intake stays possible for other factories later.
- **The ticket follows the step changes, as well as possible** (decided with @Tobias): steps without a matching ticket status leave the ticket alone.
- **Hooks** (@Tobias's idea): event-driven reactions, fired **by the orchestrator**, configured in a hooks file that is re-read without a restart. **No workflow nodes for mirroring** (decided with @Tobias). The default hook is a **declared MCP call**; a **command** hook (JSON on stdin, like Claude Code hooks) is the escape hatch (decided with @Tobias).
- **Out-of-sync detection**: the core reads the ticket's status itself, with its own credential, and raises a closed or deleted ticket of an active task to the factory owner.
- **Triage** as a **pushed headless agent step** (decided with @Tobias) that picks a route: *quick*, *small*, *project* or *park*.
- **Park means "a human must decide"**, never a backlog (decided with @Tobias): a parked task waits at a gate where an approver picks a route or cancels; the M3b stall rule makes a forgotten one visible.
- **Workflow CRUD over the core's MCP**, per step, against a draft, with **instant feedback**; illegal operations are refused **when they happen** (decided with @Tobias). Approval writes the workflow file with an increased version (decided with @Tobias).
- **The workflow moves to its own file, `workflow.yaml`**, written only by the core; comments become a `description:` field per step; `factory.yaml` stays hand-maintained and is never written by the core (decided with @Tobias).
- **Availability check** at startup (core and orchestrator) and on workflow approval.
- **Setup split**: service side (`taktloop init`, `taktloop up`) and team side (`taktloop connect`); two short user docs (decided with @Tobias: taktloop keeps building itself, the demo only shows the split).
- **Plain HTTP API** over the same operations (kept, decided with @Tobias).

**Non-goals for M4**

- A Codex (or other harness) headless start and a `bindings:` section (decided with @Tobias: not needed now). The binding-switch criterion (PRD 2.1) is shown with what exists: removing `dispatch: push` from `code_review` through the workflow CRUD moves the reviewer role from the headless bot to a person or any session (§14) **[builder call]**.
- Ticket-first intake (a new ticket creating a task), harnesses writing tickets as part of a step, a real Jira.
- Tenants, factory lifecycle, cloud hosting, a dashboard. The anti-goal below guards the path to them.
- A second product repository (decided with @Tobias).
- Cancelling a task from anywhere: cancel exists only as a route of the park gate (§7.3) **[builder call]**.
- Hot reload of `factory.yaml` (only `workflow.yaml` changes live, through the CRUD; hooks reload on their own).

**Anti-goals** (failure even if it technically works)

- A backlog: any state where a task can wait without anyone being asked to decide.
- Hooks or the workflow CRUD growing into a workflow engine: no conditions, loops or expressions in hooks; a hook is one call or one command per event.
- The core acting on the outside world: the core still only reads (gate checks, sync checks, availability); everything that writes (tickets, hooks, headless runs) is the orchestrator's.
- A live workflow that could strand a running task.
- A setup that assumes the factory directory and the taktloop source checkout are the same directory (it would block the later central service).
- Exceeding the budget: **≤ 4,300 non-test lines** under `src/` in total (cap raised by @Tobias, MVP-wide), counted as before (`wc -l` over every `.py` file under `src/`). `src/` is at 2,898, so M4 has **~1,400 lines** (~950 estimated, so ~450 headroom). The ticket stand-in counts. No new runtime dependency.

## 3. Approaches considered

**Where the ticket stand-in runs**

| Option | Trade-off |
| --- | --- |
| A. Mounted in the `serve` process at `/tickets/mcp`, own module and SQLite file | fewest processes; shares the core's lifetime |
| **B. Its own process, `taktloop tickets` (chosen)** | purest "external tool via its own MCP server" (ADR 0002); one more process to start and configure |
| C. An existing open-source tracker (beads / `bd`) | runs on Dolt (Go binary + versioned DB); breaks the setup and dependency budget; dropped |

**Chosen: B**, by @Tobias.

**What a hook runs**

| Option | Trade-off |
| --- | --- |
| Scripts only (Claude Code style) | most flexible; but each script is an adapter with its own credentials, untyped, duplicated per factory (PRD 2.3), against ADR 0002 |
| **Declared MCP call by default, command as escape hatch (chosen)** | reuses the code-step `call` shape and templating; runs with the orchestrator's credential; testable; one call per event |

**Chosen** by @Tobias.

**Where a changed workflow lives**

| Option | Trade-off |
| --- | --- |
| Core database is the truth, file only seeds version 1 | simplest; the workflow leaves git — rejected by @Tobias |
| The file in git is the truth; `propose_workflow` reads a committed file | git-native; no instant feedback per operation — rejected by @Tobias |
| **Per-step CRUD on a draft in the database; approval writes `workflow.yaml` with `version: n+1`; the database caches drafts and old versions (chosen)** | instant feedback, file stays in git; the core writes one file |

**Chosen** by @Tobias. Comments: `ruamel.yaml` (round-trips comments) rejected by @Tobias as poorly maintained; a separate comments document would drift; **comments become a `description:` field** and the file holding the workflow is written only by the core (decided with @Tobias).

## 4. Processes and files

```
service side (a factory directory, its own git repo)          team side (the product repo)
  factory.yaml     hand-maintained, commented, never written      .claude/skills/  taktloop, taktloop-triage,
  workflow.yaml    written by the core, versioned                                 taktloop-plan-review
  hooks.yaml       read by the orchestrator, reloaded live        .mcp.json        the core (and tickets)
  dev-issuer.yaml
  .env             tokens (gitignored)
  .taktloop/       databases, credentials, run logs, cursor (gitignored)

  taktloop up  →  issuer :8766 · tickets :8767 · core :8765 · orchestrator
```

- **The factory directory is separate from the product checkout.** `factory.yaml` gains `product: {checkout: <path>}` (relative to the factory directory); headless runs start there (today they start in the factory directory, M3b §6.3). For the demo the factory directory is `../taktloop-factory` next to this repo, a local git repo of its own **[builder call]**; this repo keeps only the template. The root `factory.yaml` and `dev-issuer.yaml` of this repo move into the template **[builder call]**.
- **`taktloop up`** starts the issuer, the ticket stand-in, the core and the orchestrator as child processes, prefixes each output line with the process name, and stops all on Ctrl-C or when one exits **[builder call]**. The single commands (`serve`, `orchestrator`, `tickets`, `issuer`) stay.

## 5. The ticket stand-in

`taktloop tickets [--port 8767] [--database .taktloop/tickets.db]`, run in the factory directory; an MCP server (streamable HTTP, `/mcp`) on its own SQLite file. It knows nothing about tasks, steps or workflows.

**Auth** **[builder call]**: a static bearer token, `TAKTLOOP_TICKETS_TOKEN` (environment, then `.env`), like a Jira API token. Every caller (the core, the orchestrator, optionally a harness) sends it. No roles: the stand-in is a stand-in. `taktloop init` writes a random one into `.env`.

**Tools** (all results structured):

| Tool | Args | Result |
| --- | --- | --- |
| `create_ticket` | `title`, `body` | `{id, title, status: "open", closed: false}` |
| `get_ticket` | `id` | `{id, title, body, status, closed, comments: [...]}`; unknown id → tool error |
| `list_tickets` | `include_closed=false` | `{tickets: [...]}` |
| `update_status` | `id`, `status` (free text) | the ticket; refused on a closed ticket |
| `comment` | `id`, `text` | the ticket |
| `close_ticket` | `id`, `resolution` | the ticket, `closed: true`, `status: "closed"` |
| `reopen_ticket` | `id` | the ticket, `closed: false`, `status: "open"` |
| `delete_ticket` | `id` | `{deleted: id}` |

Statuses are free text (as in Jira, where they are configured per project); `closed` is the only status the stand-in itself understands.

**A human interface** for the demo: `taktloop tickets list|show|close|reopen|delete <id>` as MCP clients of the stand-in, like the core's CLI **[builder call]**.

## 6. Hooks

### 6.1 Events the hooks see

The core gains two **core events** in the history, written in the same transaction as the change:

- `step_opened` `{step, round}` whenever a step run is opened (the start step included);
- `task_closed` `{status: done | cancelled}` when a task finishes or is cancelled.

They also make the history easier to read. Hooks can match **any** event operation (`report_stall`, `out_of_sync`, `workflow_changed`, `decide_gate`, …), only `result: ok` events **[builder call]**.

New core tool **`list_events(after_id, limit=100)`**, role `orchestrator`: events across all tasks with `id`, `at`, `subject`, `operation`, `task_id`, `step` (name of the run, if any), `detail`.

### 6.2 `hooks.yaml`

Next to `factory.yaml` **[builder call: YAML like every other config file, not JSON]**:

```yaml
hooks:
  - name: ticket-status
    on: step_opened
    map:                                     # event.step → {{mapped}}; steps not listed: no call
      triage: triage
      triage_decision: waiting for decision
      design: in design
      design_approval: waiting for approval
      plan: planning
      plan_review: in review
      workspace: in progress
      build: in progress
      code_review: in review
      verify: in review
      integration_approval: waiting for approval
      integrate: in progress
    call:
      mcp_server: tickets
      tool: update_status
      args: {id: "{{evidence.open_ticket.ticket}}", status: "{{mapped}}"}
  - name: ticket-close
    on: task_closed
    call:
      mcp_server: tickets
      tool: close_ticket
      args: {id: "{{evidence.open_ticket.ticket}}", resolution: "{{event.status}}"}
  # - name: notify-stall                     # the escape hatch, example only
  #   on: report_stall
  #   command: ./hooks/notify.sh             # event JSON on stdin, cwd = factory dir, 30 s timeout
```

- Keys per hook: `name`, `on` (an event operation), optional `step` (only events of that step), optional `map`, and exactly one of `call` (`mcp_server`, `tool`, `args`, as in code steps) or `command`.
- Placeholders: `{{task.id}}`, `{{task.title}}`, `{{event.<key>}}` (the event's detail and `step`), `{{evidence.<step>.<key>}}` (evidence of that step's latest completed run on the task), `{{mapped}}`. A placeholder that doesn't resolve for this event (for example no ticket yet, or a step not in `map`) **skips the hook for this event**, logged in one line. That is the "as well as possible" rule.
- `mcp_server` must be one of `factory.yaml`'s `mcp_servers`.

### 6.3 Firing (in the orchestrator)

- On every tick the orchestrator checks `hooks.yaml`'s modification time and re-reads it if it changed. **An invalid file is refused as a whole and the last valid one stays in use**, with one log line naming the error **[builder call]**.
- It calls `list_events(after_id=<oldest cursor>)` and runs matching hooks **in event order**. **Each hook has its own cursor** (last handled event id, by hook name), kept in `.taktloop/hooks.cursor` (JSON), so one stuck hook never holds back another. A new hook name, or the very first start, begins at the current newest event: no replay of old history **[builder call]**.
- **At least once**: a hook's cursor advances past an event only when the hook succeeded or was given up on it. A **failing** hook (tool error, command exit ≠ 0 or timeout) is retried on the next ticks, **at most 3 attempts**, then given up for that event with a log line to stdout and `.taktloop/hooks.log`. A hook whose **server is unreachable or unavailable** (§10) **waits**: no attempt is counted and its cursor stays, so after an outage the ticket catches up in order **[builder call]**. A failing hook never blocks a task.
- Command hooks get the event (with `task`) as JSON on stdin, run in the factory directory, 30 s timeout, environment without any taktloop or GitHub token **[builder call]**.

## 7. Triage, park and the ticket in the workflow

### 7.1 Workflow head

```yaml
version: 1
start: open_ticket
steps:
  open_ticket:
    kind: code
    role: steward                       # see 7.4
    description: Opens the task's ticket; its id is the evidence other parts refer to.
    call:
      mcp_server: tickets
      tool: create_ticket
      args: {title: "{{task.title}}", body: "{{task.request}}"}
      evidence: {ticket: id}
    next: triage
  triage:
    kind: agent
    role: triage
    skill: taktloop-triage
    dispatch: push
    push: {start: claude-headless, identity: triage-bot}
    instructions: >
      Read the request and look at the repository. Pick quick, small, project or park,
      with a one-line reason. Park when the request is not ready or you cannot tell.
    routes: {quick: workspace, small: plan, project: design, park: triage_decision}
  triage_decision:
    kind: human_gate
    role: approver
    description: A parked task. Someone must decide; parked tasks show as stalled.
    routes: {quick: workspace, small: plan, project: design, cancel: cancelled}
  design: …                              # as in M3b, the rest unchanged
```

- **Code-step placeholders** gain `{{task.id}}`, `{{task.title}}`, `{{task.request}}`.
- **`routes:`** (new key) on an agent step replaces `next`/`output`: the step is completed with `outcome = <route name>` and `findings` = the reason (required); an unknown route is refused. On a human gate `routes:` replaces `on_approve`/`on_reject`: `decide_gate(task_id, decision="route", target=<route name>, reason=…)`; the CLI gets `taktloop route <task> <name> --reason …` **[builder call]**.
- **`cancelled`** is a reserved target like `done`: the task gets status `cancelled` and a `task_closed` event (the close hook closes the ticket).
- **Small change** goes straight to `plan`: the plan step's instructions say "if there is no approved design doc, write the plan from the task's request"; plan review checks against the request in that case. The route's reason is in the history.
- **Quick task** goes straight to `workspace`; code review, CI green and integration approval still hold on every route. With no plan, `build`'s instructions say "implement the approved plan, or the request if the task has no plan (quick route)", and `code_review`'s "review against the plan, or the request if there is none". No separate quick-route steps **[builder call]**.

### 7.2 The triage executor

- New identity `triage-bot` with role `triage` in `dev-issuer.yaml` (template). Headless start exactly as M3b's reviewer (read-only tools).
- New skill **`taktloop-triage`** (template, installed by `connect`), modelled on brainstorming's first check: what kind of change is this, does the request say what's wanted, how much of the repo does it touch. Output: one route and one line of reason. Never asks anything.
- The bridge skill learns: a step with routes is completed with `outcome=<route>` and `findings=<reason>`.

### 7.3 Park

- The park gate is a normal human gate with routes; an approver picks a route or `cancel`.
- **Nothing rots**: the M3b stall rule 1 already flags a gate nobody decides within `stall_after_minutes`. No backlog state, no "later" status.
- **Override at intake**: `taktloop task create … --route quick` (needs `approver` in addition to `requester`). The route is stored on the task; when the first step with `routes:` opens, the core completes it at once with that route (subject: the creator, reason "route set at intake"). The ticket still gets opened first **[builder call]**.

### 7.4 Roles

`open_ticket` is run by the orchestrator, so its role must be one only the orchestrator identity holds (as `verifier` is for `verify`, M3b §4). New role **`steward`** (code steps that only keep records), added to the orchestrator identity; `verifier` is not reused because it means "checks the change" **[builder call]**. 

## 8. Out-of-sync handling

Declared in `factory.yaml` (core config; a restart changes it) **[builder call]**:

```yaml
sync:
  ticket:
    mcp_server: tickets
    tool: get_ticket
    args: {id: "{{evidence.open_ticket.ticket}}"}
    expect: {closed: false}
    every_minutes: 5
```

- New core tool **`check_sync(task_id)`**, role `orchestrator`. The orchestrator calls it for every active task, at most every `every_minutes`. The **core** resolves the placeholder, calls the tool **with its own credential**, and compares with `expect` (M3a's matching code).
- A task whose placeholder doesn't resolve (no ticket yet) is skipped. An unreachable server or transport error changes nothing (tried again next time).
- **Out of sync** = a tool error (ticket deleted) or an `expect` mismatch (ticket closed). The core records an **`out_of_sync`** event `{name: ticket, reason}` **once** while the task is out of sync; `waiting_for` appends ` — out of sync: ticket 4 is closed`; `task list` and `task show` show it.
- **Back in sync** (someone reopened the ticket): the core records `in_sync` and the flag clears **[builder call]**.
- Nothing else happens: the task keeps running. Only the factory owner decides (reopen the ticket, or cancel at a gate).
- The core's tickets credential is the same token as everyone's (§5): a known MVP limit, like the shared GitHub PAT (M3b §6.2).

## 9. Live workflow changes (breakout)

### 9.1 Files

- `factory.yaml` gets `workflow_file: workflow.yaml` (default) and **loses the `workflow:` block**; a `workflow:` block left in it is refused with a hint to move it.
- `workflow.yaml` holds `version`, `start`, `steps`. Every step may have `description:` (free text, shown in `task show` and the step context). Written by the core with `yaml.safe_dump(sort_keys=False)`, atomically (temp file + rename). Committing it to git is the owner's job.

### 9.2 Versions

- Schema v3: `workflow_versions(version, text, sha256, source, approved_by, approved_at)`, `workflow_draft` (one row: `base_version`, the draft as JSON, `updated_by`, `updated_at`), and `tasks.workflow_version`, `tasks.route`. **No migration**: a v2 database is refused as before ("delete it to start fresh"). The demo starts in a new factory directory with a new database, and old tasks ran on a workflow without `open_ticket` or `triage` **[builder call]**.
- **A task stays on the version it started with.** New tasks take the newest. The service resolves each task's workflow from a version cache instead of one global workflow.
- **Startup:** the core reads `workflow.yaml`. Same hash as the newest snapshot → nothing to do. No snapshot yet → it becomes that version. Different → the file is validated (§9.4, including availability); valid → snapshot as the next version, the file is rewritten with that version number, a `workflow_changed` event `{version, source: file}`; invalid → the core refuses to start and names the error.
- The orchestrator reads the workflow of a step's version from the core (`get_workflow(version)`), cached, instead of from its own config: after a live change its config file copy would be stale **[builder call]**. `sweep` returns each run's `workflow_version`.

### 9.3 Tools

| Tool | Role | Does |
| --- | --- | --- |
| `get_workflow(version=None, draft=false)` | any | the live (or given) version, or the draft, as data + its problem list |
| `put_step(name, definition)` | `factory_assistant` or `factory_owner` | add or replace one step in the draft (the draft starts as a copy of the live version) |
| `remove_step(name)` | same | remove a step from the draft |
| `set_start(name)` | same | change the draft's start step |
| `discard_draft()` | same | drop the draft |
| `approve_workflow(base_version)` | `factory_owner` | make the draft live (§9.5) |

Each editing call returns `{draft_version_base, problems: [...]}`. CLI: `taktloop workflow show|draft|put <name> <file.yaml>|remove|start|discard|approve` **[builder call]**. The breakout assistant uses the MCP tools directly; the bridge skill gets a short "factory assistant" section, and the template ships a **`taktloop-breakout`** skill that knows the format reference and the tools **[builder call]**.

### 9.4 Instant feedback: refused vs. problems

- **Refused at once, the draft unchanged** (`Rejected` with the reason): anything that can't be right even mid-edit — a malformed step, an unknown kind or key, a missing required field, a bad `routes` or `push`, an `mcp_server` not in `factory.yaml`, a role no identity holds **[builder call: roles known from the dev issuer config are not visible to the core; drop this check unless cheap]**, a reserved name.
- **Accepted, listed as problems**: gaps that are normal while editing — a target that doesn't exist (yet), an unreachable step, an unknown placeholder, a `review_of` pointing at a step that doesn't exist, a missing start step.
- This needs the parser split: per-step checks (refuse) and whole-graph checks (problems), both pure, both used at startup (where any problem means invalid).

### 9.5 Approval

`approve_workflow(base_version)` is refused when: the problem list isn't empty; `base_version` isn't the live version (someone else approved in between); a tool the draft names is missing on its server (§10). Otherwise, in one transaction plus the file write: snapshot as version n+1, write `workflow.yaml`, clear the draft, record `workflow_changed {version, source: mcp, by}`. New tasks use it immediately; running tasks finish on theirs.

**Which step a gate approves** (separation rule 3, strict mode): today `separation.approved_step()` takes the nearest agent work step *before the gate in file order*. Steps added through the CRUD would land at the end and silently change that. So human gates get an explicit **`approves: <step>`** key (validated: an agent work step with artifacts); a gate without it approves nothing for rule 3; review-escalation gates keep deriving it from their review step. The template sets `design_approval: design`, `integration_approval: build`. `put_step` keeps a replaced step's position and appends new ones; file order no longer means anything **[builder call]**.

Separation of duties: editing and approving are separate roles (`factory_assistant` proposes, `factory_owner` approves, `rbac.md`). In `strict` mode an owner may still approve a draft they edited themselves **[builder call]** (otherwise @Tobias couldn't edit by hand).

New identity `assistant` with `factory_assistant` in the template's `dev-issuer.yaml`.

## 10. Availability check

- **Core, at startup**: for every `mcp_servers` entry the core uses (gate checks, `sync`), list its tools and check every tool named exists. **Orchestrator, at startup**: the same for code steps and hooks.
- **A missing server or tool does not stop the process**: the core starts, refuses `create_task` with "factory not available: tickets unreachable" and re-checks at most once a minute; the orchestrator skips code steps on that server, lets hooks on it wait (§6.3), and re-checks each minute **[builder call]**.
- **On `approve_workflow`** and on reading a hand-edited file at startup: a draft naming a tool that doesn't exist on its server is refused (the server unreachable → refused too, "can't check").
- New core tool **`get_factory()`**, any role: factory name, live workflow version, draft (problems) if any, availability per server. CLI: **`taktloop status`** prints it **[builder call]** (M3b deferred `status`).

## 11. Plain HTTP API

`POST /api/<tool>` on the core's server, JSON body = the MCP tool's arguments, same bearer token and verifier, same `Service` call; `200` with the tool's JSON result, `403/409` with `{reason}` on a refusal, `401` without a valid token **[builder call on status codes]**. Every MCP tool is available, nothing else. Documented with one `curl` example.

## 12. Setup and docs

- **`taktloop init <dir> --repo <owner/repo> --checkout <path>`**: copies `templates/mvp` (`factory.yaml`, `workflow.yaml`, `hooks.yaml`, `dev-issuer.yaml`) into `<dir>`, fills in `owner`/`repo` (literal substitution in the copied files) and `product.checkout`, writes `.env` with a random `TAKTLOOP_TICKETS_TOKEN` and an empty `TAKTLOOP_GITHUB_TOKEN=` line to fill in, runs `issuer init`, `git init` if `<dir>` isn't in a repo **[builder call]**. Refuses a non-empty `<dir>`.
- **`taktloop connect <core-url> [--tickets-url …]`** in the product repo: installs the template's skills into `.claude/skills/` (today's `install-skill`, which it replaces), and adds the core (and the stand-in) to `.mcp.json` with `Authorization: Bearer ${TAKTLOOP_TOKEN}`; prints the one line to start a session (`TAKTLOOP_TOKEN=$(taktloop token agent-a --config <factory dir>/factory.yaml) claude`) **[builder call]**.
- **User docs** in `docs/user/`, short: `run-a-factory.md` (service side: init, tokens, up, status, workflow changes, hooks), `connect-your-repo.md` (team side), `workflow-reference.md` (every step key, routes, placeholders, hooks, sync; the comments that used to be in `factory.yaml`). The README shrinks to what taktloop is plus links.
- **The 30-minute check**: @Tobias follows only the docs on this machine with a fresh factory directory (§14 step 1), timed.

## 13. Code shape and budget

| Part | Where | ~Lines |
| --- | --- | --- |
| Ticket stand-in: server, store, its CLI | new `tickets.py` (+ store), `cli.py` | 150 |
| Hooks: file loading, matching, placeholders, cursor, retries, command runner | new `hooks.py`, `orchestrator.py` | 130 |
| `step_opened`/`task_closed` events, `list_events` | `ops_base.py`, `ops_orchestrator.py`, `mcp_server.py` | 25 |
| Routes, `cancelled`, route at intake, task placeholders | `workflow.py`, `ops_steps.py`, `ops_gates.py`, `service.py`, `checks.py` | 90 |
| Sync check, flag, `waiting_for` | new `ops_sync.py`, `config.py`, `rules.py` | 70 |
| Workflow CRUD, draft, versions, file write, startup reconcile, per-task workflow, schema v3, parser split, gates' `approves` | new `ops_workflow.py`, `workflow.py`, `store.py`, `serve.py` | 280 |
| Availability check, `get_factory`, `status` | `external.py`, `serve.py`, `orchestrator.py`, CLI | 60 |
| HTTP API | `mcp_server.py` or new `http_api.py` | 70 |
| `init`, `connect`, `up`, `workflow` and `route` commands | `cli.py`, `cli_commands.py` (split if over ~300) | 75 |
| **Total** | | **~950** of ~1,400 |

The headroom is for the estimates being wrong, not for new scope: the plan must carry a line allowance per task and a running count, and the HTTP API comes last so it can be squeezed. **If the count heads over the cap, stop and ask @Tobias** what to cut; nothing is dropped silently **[builder call]**. No module over ~300 lines.

## 14. M4 acceptance (the demo)

`separation: strict`, taktloop building itself, small real changes chosen by @Tobias.

1. **Setup**: following only `docs/user/`, @Tobias runs `taktloop init ../taktloop-factory …`, fills the GitHub token, `taktloop up`, and `taktloop connect` in this repo, then creates a task. Timed: under 30 minutes. `taktloop status` shows all servers available.
2. **Ticket + triage**: on `task create`, `open_ticket` opens a ticket (`taktloop tickets show`), the headless triage bot picks a route with a reason. The ticket's status follows the steps (`tickets show` after each step change).
3. **Quick task**: a one-line change routed *quick* goes workspace → build → code review → verify → integration approval → integrate → done; the ticket ends closed.
4. **Park**: a vague request is parked; after the (lowered) stall time `task list` shows it stalled; @Tobias routes it to *project* with `taktloop route`.
5. **Out of sync**: closing (then deleting) the ticket of an active task shows *out of sync* in `task list` within `every_minutes`, one `out_of_sync` event; reopening clears it.
6. **Breakout**: in a session as `assistant`, an agent adds a step (for example a `docs_check` agent step between `build` and `code_review`) through the CRUD tools; an intermediate state shows problems, an illegal call is refused at once; @Tobias approves; `workflow.yaml` has `version: 2` and the new step; a running task finishes on version 1, a new task goes through the new step.
7. **Binding switch**: through the CRUD, `code_review` loses `dispatch: push`; the next review is pulled by a session or done by @Tobias with `taktloop review`; nothing else changed.
8. **Hooks live**: editing `hooks.yaml` (for example removing a `map` entry) takes effect without a restart; a deliberately broken hook is retried three times and given up, the task unaffected.
9. **Availability**: with the stand-in stopped, a restarted core refuses `create_task` naming `tickets`; approving a draft that names a non-existent tool is refused.
10. **HTTP API**: one `curl` creates a task and one reads it.
11. **Budget**: the lightweight-budget table (PRD §5) filled in with measured values in `docs/user/` or the README: components, lines, dependencies, processes, setup time.

**Automated tests**: stand-in tools and token; hooks file parsing, reload, invalid-file fallback, placeholder skip, map, cursor, retry/give-up, command hook (fake script); `step_opened`/`task_closed` events and `list_events`; routes on agent steps and gates, unknown route refused, `cancelled`, route at intake; sync check (in sync, closed, deleted, unreachable, once, back in sync); CRUD refusals vs problems, approve refused with problems / stale base / missing tool, file written with version n+1, running task stays on its version; startup reconcile (same, hand-edited valid, invalid); `approves` and separation rule 3 after a step is added; availability (missing server, missing tool, create_task refused); HTTP API (ok, refused, 401); `init` and `connect` into temp dirs. The orchestrator tests use a real core with fake external MCP servers as in M3b.

## 15. Docs to update

- **PRD §5**: the MVP budget is ~4,300 lines (raised by @Tobias); the runtime row: the core is one process with one SQLite file, the stand-ins (issuer, tickets) and the orchestrator are separate processes.
- **ADR 0002**: the ticket stand-in is its own process; the core's out-of-sync check; the availability check is implemented.
- **ADR 0004**: hooks are fired by the orchestrator from the core's events.
- **mvp-workflow.md**: triage routes and park as built; the small-change route goes to plan.
- **rbac.md**: `factory_assistant` and `factory_owner` as used by the workflow tools; `triage`, `steward`.
- **system-overview.html**: the tickets process, hooks.

## 16. Constraints

- Everything from M1 §8, M2 §10, M3a §10 and M3b §11 still holds.
- Direct runtime dependencies unchanged: `mcp`, `pyyaml`, `pyjwt[crypto]`.
- No token is written to logs, events, run logs, hooks' stdin or `workflow.yaml`.

## 17. Left to the builder

Module names and split; pydantic models of the new tools; the stand-in's schema; log formats; the CLI's exact flags and output; skill wording (bridge, triage, breakout) within this design; the doc texts; the ticket status names in the template's `hooks.yaml`.

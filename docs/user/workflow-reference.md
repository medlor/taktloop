# Workflow reference

The formats of a factory directory's files: `workflow.yaml` (the steps),
`hooks.yaml` (reactions to events) and `factory.yaml` (the factory itself,
including the `sync:` check). The template in `templates/mvp` uses all of
them.

## workflow.yaml

Written by the core (on approval of a draft, or when a hand edit is picked up
at startup); YAML comments do not survive, so every step says what it is for
in `description`.

```yaml
version: 3                 # set by the core
start: open_ticket         # the first step of every new task
steps:
  <name>: {kind: ..., role: ..., ...}
```

Step names are free (`done` and `cancelled` are reserved). Targets name a
step, or `done` (the task is finished) or `cancelled` (the task is cancelled).
Both end the task with a `task_closed` event.

### Keys every step has

| Key | Meaning |
| --- | --- |
| `kind` | `agent` (a session or headless run does it), `human_gate` (someone decides), `code` (the orchestrator makes one MCP call) |
| `role` | the role a caller needs to claim or decide it |
| `description` | one line: what the step is for. Shown in `task show`, the step context and `workflow show` |

### Agent steps (`kind: agent`)

| Key | Meaning |
| --- | --- |
| `skill` | the skill the executor runs (required) |
| `instructions` | text for the executor, on top of the skill |
| `output: {artifacts: [name, ...]}` | the artifacts `complete_step` must name (repo path, full commit SHA, branch; `path: "."` for a commit) |
| `next` | the step after it |
| `dispatch: push` + `push: {start: claude-headless, identity: <id>}` | the orchestrator starts a read-only headless Claude Code run as that identity, in the product checkout. Default `dispatch: pull`: sessions pull it |

A **review step** has `review_of: <agent work step>` instead of `output` and
`next`: it is completed with `outcome: pass` or `issues` (and `findings`).

| Key | Meaning |
| --- | --- |
| `review_of` | the step whose latest artifacts are reviewed |
| `on_pass` | where a pass goes |
| `on_issues` | where issues go (that step reopens with the findings as feedback) |
| `max_issue_rounds` + `on_escalate` | after this many `issues` verdicts, go to this gate instead |

A **routing step** has `routes: {<route>: <target>, ...}` instead of `output`
and `next`. It is completed with `outcome: <route>` and `findings: <reason>`
(required); an unknown route is refused. Route names are lower case
(`quick`, `small`). `taktloop task create --route <route>` (approver only)
sets the route at intake: the first routing step is then completed at once.

An agent work step may also carry a **check** (`check`, `evidence`,
`on_fail`, as on code steps below): the executor names the evidence, the core
checks it.

### Human gates (`kind: human_gate`)

| Key | Meaning |
| --- | --- |
| `on_approve` | where `taktloop approve` goes |
| `on_reject` | a step or a list; the first is the default of `taktloop reject`, `--to` picks another |
| `approves` | the agent work step whose work this gate approves (strict separation: whoever did that work may not approve). Without it the gate approves nothing for that rule, unless it is a review's escalation gate |
| `routes` | instead of `on_approve`/`on_reject`: `taktloop route <task> <route> --reason ...` |

### Code steps (`kind: code`)

The orchestrator claims the step, makes one MCP call with its own credential,
and completes the step with evidence taken from the result.

```yaml
verify:
  kind: code
  role: verifier                    # a role only the orchestrator's identity holds
  call:
    mcp_server: github              # one of factory.yaml's mcp_servers
    tool: actions_list
    args: {owner: medlor, repo: taktloop, workflow_runs_filter: {branch: "{{artifacts.change.branch}}"}}
    evidence: {ci_run: workflow_runs.workflow_runs.0.id}   # evidence key: dotted path into the result
  evidence: [ci_run]                # optional: the evidence keys the check needs
  check:                            # optional hard gate, run by the core with its own credential
    mcp_server: github
    tool: actions_get
    args: {resource_id: "{{evidence.ci_run}}"}
    match: {workflow_run.head_sha: "{{artifacts.change.commit}}"}   # else: refused, nothing changes
    expect: {workflow_run.conclusion: success}                      # else: back to on_fail
  on_fail: build
  next: integration_approval
```

A refused completion (a `match` mismatch, the call or check unavailable) is
retried every `code_retry_seconds`; an `expect` mismatch goes to `on_fail`
with the observed values as feedback.

### Placeholders

`{{...}}` in `args`, `match` and `expect`:

| Placeholder | Where | Value |
| --- | --- | --- |
| `{{task.id}}`, `{{task.title}}`, `{{task.request}}` | calls, checks, sync, hooks | the task's fields |
| `{{artifacts.<name>.commit\|branch\|path}}` | calls, checks | the latest round of that artifact |
| `{{evidence.<key>}}` | a step's own check | the evidence its executor named |
| `{{evidence.<step>.<key>}}` | calls, sync, hooks | evidence of that step's latest completed run, for example `{{evidence.open_ticket.ticket}}` |
| `{{event.<key>}}`, `{{mapped}}` | hooks | the event's detail and step; the hook's `map` value |

## hooks.yaml

Next to `factory.yaml`, read by the orchestrator, re-read on change.

```yaml
hooks:
  - name: ticket-status            # unique; also the name of the hook's cursor
    on: step_opened                # an event operation
    step: build                    # optional: only events of this step
    map: {build: in progress}      # optional: event.step -> {{mapped}}; other steps: no call
    call:                          # exactly one of call or command
      mcp_server: tickets
      tool: update_status
      args: {id: "{{evidence.open_ticket.ticket}}", status: "{{mapped}}"}
  - name: notify-stall
    on: report_stall
    command: ./hooks/notify.sh     # event JSON on stdin, cwd = factory dir, 30 s, no taktloop or GitHub token in its env
```

- **Events**: any `ok` event of the core, for example `step_opened {step,
  round}`, `task_closed {status: done | cancelled}`, `report_stall`,
  `out_of_sync`, `in_sync`, `workflow_changed`, `decide_gate`,
  `complete_step`.
- A placeholder that doesn't resolve for an event (no ticket yet, a step not
  in `map`) **skips** the hook for that event, with one log line.
- `on:` needs no quotes (YAML reads a bare `on` as `true`; taktloop reads it
  back as `on`).
- Delivery: at least once, in order, three attempts per event, then given up
  (logged to `.taktloop/hooks.log`); a hook whose server is unavailable waits.
  A new hook starts at the newest event.

## factory.yaml

Hand-maintained; read by the core and the orchestrator at startup (restart
after a change).

| Key | Meaning |
| --- | --- |
| `factory` | the factory's name; token scopes are `taktloop:<factory>:<role>` |
| `server: {host, port}` | where the core listens (default `127.0.0.1:8765`) |
| `database` | the core's SQLite file (default `.taktloop/factory.db`) |
| `auth: {issuer, audience}` | the token issuer the core trusts, and the audience tokens must carry |
| `cli: {identity}` | who the CLI acts as by default |
| `separation` | `off` (default) or `strict`: an identity may not review its own work, and may not approve work it planned or built (the gate's `approves` step) |
| `lease_minutes` | how long a claim lasts without a heartbeat (default 30) |
| `workflow_file` | the workflow (default `workflow.yaml`) |
| `product: {checkout}` | the product repo's checkout, relative to this file; headless runs start there |
| `mcp_servers: {<name>: {url, token_env}}` | MCP servers the core and the orchestrator call; the token comes from that environment variable, then from `.env` next to this file |
| `sync: {<name>: {mcp_server, tool, args, expect, every_minutes}}` | the core's out-of-sync check per active task (below) |
| `orchestrator` | `identity`, `tick_seconds` (15), `code_retry_seconds` (60), `stall_after_minutes` (60), `max_lease_expiries` (2) |

### sync

```yaml
sync:
  ticket:
    mcp_server: tickets
    tool: get_ticket
    args: {id: "{{evidence.open_ticket.ticket}}"}
    expect: {closed: false}
    every_minutes: 5               # default 5; a whole number of minutes
```

The orchestrator asks the core to check each active task at most every
`every_minutes`. A task whose placeholder doesn't resolve yet (no ticket) is
skipped; an unreachable server changes nothing. A tool error (the ticket was
deleted) or an `expect` mismatch (closed) records `out_of_sync` once and shows
in `task list`; a later match records `in_sync` and clears it. The task keeps
running either way.

### Stalls

The orchestrator reports a step as stalled (`report_stall`, shown in `task
list`) when nobody picked it up, or nobody decided a gate, for
`stall_after_minutes`; when its lease expired or its headless start failed
`max_lease_expiries` times; or when a code step isn't through after
`stall_after_minutes`.

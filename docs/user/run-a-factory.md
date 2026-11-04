# Run a factory

This is the **service side**: one directory per factory, with its config, its
tokens and its databases, and the four processes that run it. Teams connect
their product repositories to it ([connect-your-repo.md](connect-your-repo.md)).
The factory directory is **not** the product checkout and not the taktloop
source checkout; it is a small git repository of its own.

## 1. Install taktloop

You need [uv](https://docs.astral.sh/uv/), git, and
[Claude Code](https://docs.claude.com/en/docs/claude-code) logged in (`claude
auth status`) for the headless runs (triage and code review).

```sh
git clone https://github.com/medlor/taktloop.git
uv tool install --editable ./taktloop      # puts `taktloop` on your PATH
cd taktloop                                # here taktloop builds itself: the clone is also the product repo
```

Install it **editable**: `taktloop init` and `taktloop connect` copy the
templates and skills from the checkout (`templates/mvp`); without them they
refuse with a hint. The commands below run in the **product repo's
checkout** (for another product: `cd` into its checkout instead) unless they
say `cd ../taktloop-factory`.

The product repo needs a GitHub Actions workflow at
`.github/workflows/ci.yml` that runs on pushes to the task branches
(`taktloop/<id>-<name>`): the `verify` gate waits for its run on the change
commit and needs it green.

## 2. Create the factory directory

```sh
taktloop init ../taktloop-factory --repo medlor/taktloop --checkout .
```

- `--repo` is the product repository on GitHub (`OWNER/REPO`); the CI gate
  looks up its Actions runs.
- `--checkout` is the product repository's local checkout (here `.`, the
  clone). Headless runs start there, so it must be connected (§5) before the
  first task. It is stored relative to the factory directory (`product:
  {checkout: ../taktloop}`).

`init` refuses a directory that isn't empty. It writes:

| File | What it is |
| --- | --- |
| `factory.yaml` | the factory's config, hand-maintained; the core never writes it ([reference](workflow-reference.md#factoryyaml)) |
| `workflow.yaml` | the workflow, written **only by the core** from now on (§7) |
| `hooks.yaml` | the hooks, re-read on change (§8) |
| `dev-issuer.yaml` | the identities and their roles, for the dev token issuer |
| `.env` | `TAKTLOOP_TICKETS_TOKEN` (random) and an empty `TAKTLOOP_GITHUB_TOKEN=` (gitignored) |
| `.gitignore` | `.env` and `.taktloop/` |
| `.taktloop/` | the issuer's signing key, the credentials per identity, later the databases, run logs and hook cursor |

It also runs `git init` unless the directory is already inside a git
repository. Commit the files; committing `workflow.yaml` after a change is the
owner's job.

## 3. The GitHub token

The core checks that CI is green on GitHub itself, with its own read-only
token (agents never see it; they push with their own `gh` login). The
orchestrator uses the same token to find the CI run.

1. GitHub → Settings → Developer settings → Personal access tokens →
   Fine-grained tokens → **Generate new token**.
2. Repository access: **Only select repositories**, the product repository.
3. Repository permissions: **Actions: Read-only** (Metadata: Read-only is
   added automatically). Nothing else.
4. Put it after `TAKTLOOP_GITHUB_TOKEN=` in the factory's `.env`.

Tokens are read from the environment first, then from `.env` next to
`factory.yaml`, at startup only: restart after a change. No token is ever
written to `factory.yaml`, `workflow.yaml`, the databases, logs, events, run
logs or hooks.

## 4. Start it

```sh
cd ../taktloop-factory
taktloop up                                # stays in the foreground
```

`up` starts four processes and prefixes each output line with its name:

| Prefix | Process | Port |
| --- | --- | --- |
| `issuer\|` | the dev token issuer (development only) | 8766 |
| `tickets\|` | the ticket stand-in (`.taktloop/tickets.db`) | 8767 |
| `core\|` | the taktloop core (`.taktloop/factory.db`) | 8765 |
| `orch\|` | the orchestrator: code steps, headless runs, stalls, sync checks, hooks | – |

Ctrl-C stops all of them. If one exits, `up` stops the others and exits with
1; its last lines say why. Each also runs on its own (`taktloop issuer`,
`taktloop tickets serve`, `taktloop serve`, `taktloop orchestrator`), all
from the factory directory.

Check that every server the workflow needs is there:

```sh
taktloop status
```

```
factory taktloop, workflow version 1
  github: ok
  tickets: ok
```

While a server is missing (not running, no token, a tool it lacks), the core
refuses `task create` naming it, and the orchestrator waits with the steps
and hooks that need it.

Leave `up` running in this terminal and use a **second terminal** for
everything below.

## 5. Connect the product repo

In the product repo's checkout (here the taktloop clone), before the first
task:

```sh
taktloop connect http://127.0.0.1:8765/mcp --tickets-url http://127.0.0.1:8767/mcp
```

It installs the skills into `.claude/skills/` and the servers into
`.mcp.json`; commit both. Sessions and the headless runs use them. Details,
identities and troubleshooting: [connect-your-repo.md](connect-your-repo.md).

## 6. Day to day

Run the CLI in the factory directory (`cd ../taktloop-factory`), or add
`--config ../taktloop-factory/factory.yaml`.
It acts as `cli.identity` from `factory.yaml` (`tobias`), or `--as
<identity>`, or the token in `TAKTLOOP_TOKEN`.

```sh
taktloop task create "Add a --version flag" --request "..."   # as tobias
taktloop task list                     # waiting for what, stalled, out of sync
taktloop task show 1                   # the whole history
taktloop tickets show 1                # the task's ticket
taktloop approve 1                     # or: reject 1 --reason "..." [--to plan]
taktloop route 1 quick --reason "..."  # a parked task: quick, small, project or cancel
taktloop task create "Fix a typo" --route quick   # skip triage (approver only)
```

**What happens to a new task.** The orchestrator opens its ticket
(`open_ticket`), then starts a headless triage run as `triage-bot`, which picks
a route with a one-line reason: **quick** (straight to workspace and build),
**small** (plan from the request), **project** (design first) or **park**. A
parked task waits at `triage_decision` until an approver routes or cancels it;
after `stall_after_minutes` it shows as stalled. Code review, CI green and the
integration approval hold on every route. Agent steps are pulled by sessions
in the product repo ([connect-your-repo.md](connect-your-repo.md));
`code_review` is pushed to a headless run as `reviewer-bot`.

**Strict separation.** With `separation: strict` in `factory.yaml` (restart
the core), nobody reviews or approves their own work; the refusal names the
earlier step.

**Out of sync.** Every `every_minutes` (`sync:` in `factory.yaml`) the core
reads each active task's ticket with its own credential. A ticket closed or
deleted behind the task's back shows as `out of sync` in `task list` and
`task show`, with one `out_of_sync` event; the task keeps running. Reopen the
ticket (`taktloop tickets reopen 1`) to clear it, or cancel the task at a gate.

**Plain HTTP.** The core also answers `POST /api/<tool>` with a JSON body of
the tool's arguments (200, 401 without a valid token, 404 unknown tool, 409
refused, 422 bad arguments):

```sh
TOKEN=$(taktloop token tobias)
curl -s -X POST localhost:8765/api/create_task -H "Authorization: Bearer $TOKEN" \
     -H 'Content-Type: application/json' -d '{"title": "Try the API", "request": "..."}'
curl -s -X POST localhost:8765/api/get_task -H "Authorization: Bearer $TOKEN" -d '{"task_id": 1}'
```

## 7. Change the workflow while it runs

`workflow.yaml` changes only through the core, against a **draft**, with
instant feedback. Tasks already running finish on the version they started
with; new tasks take the newest.

```sh
taktloop workflow show                 # the live version
taktloop workflow put docs_check step.yaml   # add or replace one step in the draft
taktloop workflow remove docs_check    # remove a step
taktloop workflow start open_ticket    # change the start step
taktloop workflow draft                # the draft and its problems
taktloop workflow approve              # make it the next version (factory owner)
taktloop workflow discard
```

A call that can't be right (unknown key, missing `role`, an `mcp_server` the
factory doesn't have, a reserved name) is **refused** and changes nothing.
Gaps that are normal mid-edit (a target that doesn't exist yet, an
unreachable step) are listed as **problems**; a draft with problems can't be
approved, nor can one whose tools are missing on their server, or one based on
an older version than the live one. Approval writes `workflow.yaml` with
`version: n+1`.

An agent can do the editing: start a session in the product repo as
`assistant` (role `factory_assistant`) and ask for the change; the
`taktloop-breakout` skill drafts it and asks you to approve.

A hand edit of `workflow.yaml` is picked up when the core starts: valid →
the next version; invalid → the core refuses to start and names the problem.

Format: [workflow-reference.md](workflow-reference.md).

## 8. Hooks

`hooks.yaml` turns core events into one MCP call (or one command) each,
fired by the orchestrator. The template keeps the ticket in line: its status
follows the steps (`ticket-status`, a `map` from step to status) and it is
closed when the task ends (`ticket-close`). Edit the file at any time: the
orchestrator re-reads it on the next tick. An invalid file is refused as a
whole, with one log line, and the last valid one stays in use. `on:` works as
written; nothing in the file needs quoting beyond normal YAML.

Each hook runs **at least once** per matching event, in order. A failing call
(tool error, command exit ≠ 0 or timeout) is retried on the next ticks, three
attempts in all, then given up with a line in the log and in
`.taktloop/hooks.log`; the task is never held up. A hook whose server is down
waits and catches up in order. A new hook starts at the newest event (no
replay). Format: [workflow-reference.md](workflow-reference.md#hooks).

## 9. Known limits (MVP)

- **The dev issuer is for development.** All credentials files, including the
  approver's, sit in `.taktloop/credentials/`. A session that can run shell
  commands on this machine could fetch another identity's token; only the
  bridge skill's "never try another identity" stands in the way.
- **Shared tokens.** The core's tickets credential is the same
  `TAKTLOOP_TICKETS_TOKEN` everyone uses, and the core and the orchestrator
  share the read-only GitHub token.
- **`open_ticket` is at least once.** If the stand-in creates the ticket but
  the step's completion then fails (the core restarts at that moment), the
  retry opens a second ticket. Delete the orphan with `taktloop tickets
  delete <id>`.
- **The CI configuration lives on the task branch.** The gate pins
  `.github/workflows/ci.yml` and checks the run of the reviewed commit, but a
  change that weakens `ci.yml` itself is only caught by code review. The merge
  commit is recorded, not verified.
- **Headless runs** (`.taktloop/runs/<step>-<n>.log`) are read-only: they may
  read files and run `git diff|log|show|rev-parse`, ignore your user-level
  Claude settings, and cannot read the factory's `.taktloop/` or `.env` (denied
  by absolute path, since they start in the product checkout). With a claude.ai
  login the orchestrator drops `ANTHROPIC_API_KEY` from their environment;
  without either, the start fails and counts towards the stall rule.
- **No migrations.** A database from an older version is refused at start:
  delete both `.taktloop/factory.db` and `.taktloop/hooks.cursor` and start
  again. A cursor left behind points past the new database's events, and the
  hooks would never fire.

## 10. Lightweight budget (PRD §5)

Measured at the end of M4:

| Criterion | Target | Measured |
| --- | --- | --- |
| Components | fewer than 10 | 6: core (MCP server, HTTP API, SQLite store), orchestrator, ticket stand-in, dev issuer, CLI, skills (bridge, triage, plan review, breakout) |
| Code size | at most ~4,300 lines under `src/` | 4,213 lines (`wc -l` over every `.py` file under `src/`) |
| Dependencies | at most ~10 runtime dependencies | 3 direct: `mcp`, `pyyaml`, `pyjwt[crypto]` |
| Runtime | the core is one process with one SQLite file | 4 processes with `taktloop up` (issuer, tickets, core, orchestrator); the core has one SQLite file, the stand-in its own |
| Setup | a new factory in under 30 minutes | _to be timed by @Tobias, following only these docs_ |

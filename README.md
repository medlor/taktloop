taktloop

taktloop moves software-development tasks through a workflow you define in
YAML: idea, triage, design, plan, build, review, CI, merge. Agents (Claude
Code sessions, headless runs) and people do the steps; taktloop's core keeps
the state, checks every caller's role from an OAuth token, holds the gates (a
human approval, a review verdict, CI green checked by the core itself on
GitHub) and records who did what. Everything goes through MCP tools (and a
plain HTTP API); taktloop has no agent loop and no tracker of its own.

## Quick start

A factory runs in its own directory (the service side); your product repo
connects to it (the team side).

Install once, from a clone of this repo: `uv tool install --editable
./taktloop`. Then, in your product repo's checkout (it needs a
`.github/workflows/ci.yml` that runs on pushed branches):

```sh
taktloop init ../my-factory --repo OWNER/REPO --checkout .
# put a read-only GitHub token after TAKTLOOP_GITHUB_TOKEN= in ../my-factory/.env
(cd ../my-factory && taktloop up)    # issuer, tickets, core, orchestrator; blocks until Ctrl-C
```

In a second terminal, again in the product repo's checkout:

```sh
taktloop connect http://127.0.0.1:8765/mcp --tickets-url http://127.0.0.1:8767/mcp   # skills + .mcp.json
taktloop --config ../my-factory/factory.yaml task create "Add a --version flag"
TAKTLOOP_TOKEN=$(taktloop token agent-a --config ../my-factory/factory.yaml) claude   # say "work on taktloop"
```

## Docs

- [Run a factory](docs/user/run-a-factory.md): init, tokens, `up`, status,
  day-to-day commands, workflow changes, hooks, known limits, the
  lightweight budget.
- [Connect your repo](docs/user/connect-your-repo.md): `connect`, sessions,
  identities, troubleshooting.
- [Workflow reference](docs/user/workflow-reference.md): every key of
  `workflow.yaml`, `hooks.yaml` and `factory.yaml`, placeholders, routes,
  checks and sync. The CI gate is declared as data in `workflow.yaml`
  (`verify.call` and `verify.check`).
- Background: [PRD](docs/prd.md), [MVP workflow](docs/mvp-workflow.md),
  [roles](docs/rbac.md), [ADRs](docs/adr/), [system overview](docs/system-overview.html).

## Development

```sh
uv sync
uv run pytest            # unit and end-to-end tests (e2e: real issuer, core and `taktloop up` subprocesses)
uv run pytest -m "not e2e"
```

One live test against GitHub's real MCP server runs only when
`TAKTLOOP_GITHUB_TOKEN` is set in the environment or in `.env`; CI skips it.
The tests written against the M3b workflow use a frozen copy in
`tests/fixtures/m3b/`; the shipped template is `templates/mvp/`.

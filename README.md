taktloop

## M2: try it

Every person and agent has a named identity with roles, defined in
`dev-issuer.yaml`: `tobias` (requester, approver, reviewer, factory owner),
`agent-a` and `agent-b` (planner, builder, reviewer, verifier). A small dev issuer hands out
tokens; the core accepts only calls with a valid token and checks the
caller's roles on every operation.

### Setup

```sh
uv sync                                  # install dependencies
uv run taktloop issuer init              # signing key + .taktloop/credentials/<identity>.json for each identity
claude mcp remove taktloop -s local      # only if you registered the core for M1 (see below)
uv run taktloop install-skill            # copy both skills to .claude/skills/ (taktloop, taktloop-plan-review)
```

Start the dev issuer and the core, each in its own terminal (keep both open):

```sh
uv run taktloop issuer                   # dev issuer on 127.0.0.1:8766 (development only)
uv run taktloop serve                    # core on 127.0.0.1:8765
```

Claude Code finds the core through the committed `.mcp.json`, which sends
the token from `TAKTLOOP_TOKEN`. Start every agent session as one identity:

```sh
TAKTLOOP_TOKEN=$(uv run taktloop token agent-a) claude
```

On first start Claude Code asks whether to use the project's `.mcp.json`
server; accept it. Tokens last 12 hours. When a session's calls fail with 401,
the token is missing or expired: restart the session the same way to get a
fresh one.

If `/mcp` shows taktloop as **needs authentication** (or "SDK auth failed ...
authorization_endpoint"), Claude Code connected without the token and fell
back to browser OAuth, which the dev issuer doesn't offer. Check, in this
order: an older registration without the header shadows `.mcp.json`
(`claude mcp get taktloop` shows its config location; remove it with
`claude mcp remove taktloop -s local`); `TAKTLOOP_TOKEN` was empty (the
`taktloop token` command failed); the token expired.

The CLI is a client too. It uses `TAKTLOOP_TOKEN` if set, otherwise
`--as <identity>`, otherwise `cli.identity` from `factory.yaml` (`tobias`).
Credentials are read from `.taktloop/credentials/` next to `factory.yaml`
(next to `dev-issuer.yaml` for `issuer init`), whatever the current
directory; `--credentials DIR` overrides it.

### The demo

Set `separation: strict` in `factory.yaml` and restart the core. Then:

```sh
uv run taktloop task create "Add a --version flag to the CLI"   # as tobias; task 1
```

1. A session as `agent-a`: say **work on taktloop**. It runs design and stops.
   Approve it: `uv run taktloop approve 1` (or `reject 1 --reason "..."`).
2. A session as `agent-a` writes the plan and stops. The `plan_review` step opens.
3. A session as `agent-a` tries to claim `plan_review` and is refused:
   "strict separation: agent-a completed plan round 1 of task 1".
4. A session as `agent-b` reviews the plan with the `taktloop-plan-review`
   skill and returns *pass* or *issues* with findings. On *issues*, the plan
   reopens for `agent-a` with the findings as `feedback`. After two *issues*
   verdicts the task waits at `plan_escalation` for an approver.
5. At the escalation gate, `uv run taktloop approve 1` or
   `uv run taktloop override 1 --reason "..."` approves the latest plan;
   `uv run taktloop reject 1 --to plan --reason "..."` sends it back.
6. `uv run taktloop task show 1` lists every event with identity, roles and
   executor, for example `agent-b (claude-code)`: the refused claim, both
   verdicts (`issues → plan`, `issues → plan_escalation`), the core's own
   `escalate` event and the override.

A call without a token is refused: `curl -i -X POST http://127.0.0.1:8765/mcp`
answers `401 Unauthorized`.

Now set `separation: off` (the default) and restart the core. One identity
may plan, review its own plan and revise it; role checks still apply.
Create task 2 and run its design and plan as `agent-a` as above; a session as
`agent-a` may now claim its `plan_review`. `tobias` holds the reviewer role,
so he can also pass the verdict from the CLI: `uv run taktloop review 2 --pass`
or `--issues "..."`.

### Other harnesses

Codex CLI (`~/.codex/config.toml`):

```toml
[mcp_servers.taktloop]
url = "http://127.0.0.1:8765/mcp"
bearer_token_env_var = "TAKTLOOP_TOKEN"
```

Pi (remote MCP server entry):

```json
{"taktloop": {"url": "http://127.0.0.1:8765/mcp",
              "headers": {"Authorization": "Bearer ${TAKTLOOP_TOKEN}"}}}
```

Start them the same way, for example `TAKTLOOP_TOKEN=$(uv run taktloop token agent-b) codex`.

### Notes

- **M1 database:** an M1 `.taktloop/factory.db` is refused at start (the
  schema changed and there are no migrations). Delete `.taktloop/factory.db`
  and start the core again.
- The M1 registration `claude mcp add ... taktloop` is no longer needed and
  must go: it has no `Authorization` header and, registered with local scope,
  it takes precedence over `.mcp.json`. Remove it with
  `claude mcp remove taktloop -s local`.
- **Known limit (accepted for M2):** all credentials files, including
  `tobias`'s, sit in the repo's `.taktloop/credentials/`. An agent session has
  a shell, so it could run `taktloop token tobias` and act as the approver,
  which would also get around strict separation. Only the bridge skill's
  "never try another identity" stands in the way. Keeping human credentials
  away from agents (another location, another machine, or a real identity
  provider) is left for later milestones.
- `.taktloop/` is gitignored: keys, secret hashes, credentials and the database
  never land in git.

## M3a: plan to merge, with a CI gate

After the plan passes review, a task goes on to code on this repository:
**workspace → build → code review → verify → integration approval →
integrate → done**. `agent-a` and `agent-b` may build, review and verify;
`tobias` approves at the gates. The transition out of `verify` is a **hard
gate**: the verifier only names the GitHub Actions run, and the core asks
GitHub itself, through GitHub's MCP server with its own read-only token,
whether that run is finished, belongs to the reviewed commit, ran
`.github/workflows/ci.yml` and succeeded. The check is declared as data
(`verify.check` in `factory.yaml`).

### The core's GitHub token

The core needs its own fine-grained personal access token (agents never see
it; they push and look up runs with their own `gh` login):

1. GitHub → Settings → Developer settings → Personal access tokens →
   Fine-grained tokens → **Generate new token**.
2. Resource owner `medlor`; Repository access: **Only select repositories**,
   `medlor/taktloop` only.
3. Repository permissions: **Actions: Read-only** and **Metadata:
   Read-only** (added automatically). Nothing else.
4. Put it in a `.env` file next to `factory.yaml` (gitignored):

   ```sh
   TAKTLOOP_GITHUB_TOKEN=github_pat_...
   ```

The core reads `TAKTLOOP_GITHUB_TOKEN` from its environment first, then from
`.env`. Without it the core still starts (with a warning), and every CI check
is refused with "the core has no credential for github
(TAKTLOOP_GITHUB_TOKEN is not set)". The token is never written to
`factory.yaml`, the database, logs or events.

### The demo

Restart the issuer (agents gained the builder and verifier roles) and the
core, with `separation: strict` in `factory.yaml`. Start new agent sessions
for fresh tokens. Pick a small real change and take it through design, plan
and plan review as in M2; when the plan passes, the task waits at
`workspace`. Then, saying **work on taktloop** in each session:

1. `agent-a` creates the branch `taktloop/<task id>-<short-name>` from
   `origin/main`, pushes it and completes `workspace` with the base commit.
2. `agent-a` builds, pushes and completes `build` with the change commit.
3. `agent-a` is refused `code_review` (strict); `agent-b` reviews
   `workspace..change`. *Issues* reopen `build` with the findings; after two
   *issues* verdicts the task waits at `build_escalation`.
4. `agent-b` verifies: runs the tests, finds the run with
   `gh run list --commit <sha>`, waits, and completes with
   `evidence={"ci_run": "<id>"}`. While the run is going, the core refuses
   ("status is in_progress, expected completed"); a run of an older commit
   is refused ("head_sha is ..., expected ..."). A red run sends the task
   back to `build` with `check_failed` feedback naming the run, then through
   review and verify again. A green run opens `integration_approval`.
5. `uv run taktloop approve <task>` (as `tobias`, who must not have built
   the change); `agent-a` merges the change commit into `main`, pushes `main`
   and completes `integrate` with the merge commit. The task is done.
6. `uv run taktloop task show <task>` shows the lifecycle: identities,
   verdicts, every `check` event by `core` (`pass: ...`, `fail: ...`,
   `mismatch: ...`), `failed → build` for a red round, and the merge commit.

### Known limits (M3a)

- **The CI configuration lives on the task branch.** The gate pins the
  workflow file (`.github/workflows/ci.yml`), so an always-green workflow
  added next to it does not count, but a change to `ci.yml` itself that
  weakens the tests passes the gate. Code review is the only guard for now;
  protected CI configuration comes later with branch protection.
- **The merge is not verified.** The core records the merge commit from
  `integrate` but does not check that `main` contains it or that nothing
  else was merged. Branch protection with required PR approval is planned
  for later.
- Every step is still pulled by hand from a session; the core has no
  background loop and does not detect stalls (M3b).

`uv run pytest` runs the tests, including end-to-end tests over a real issuer
and core (the M3a one against a fake GitHub MCP server). One live test against
GitHub's real MCP server runs only when `TAKTLOOP_GITHUB_TOKEN` is set in the
environment or `.env`; CI skips it.

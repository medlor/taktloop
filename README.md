taktloop

## M2: try it

Every person and agent has a named identity with roles, defined in
`dev-issuer.yaml`: `tobias` (requester, approver, reviewer, factory owner),
`agent-a` and `agent-b` (planner, reviewer). A small dev issuer hands out
tokens; the core accepts only calls with a valid token and checks the
caller's roles on every operation.

### Setup

```sh
uv sync                                  # install dependencies
uv run taktloop issuer init              # signing key + .taktloop/credentials/<identity>.json for each identity
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

Tokens last 12 hours. When a session's calls fail with 401, the token is
missing or expired: restart the session the same way to get a fresh one.

The CLI is a client too. It uses `TAKTLOOP_TOKEN` if set, otherwise
`--as <identity>`, otherwise `cli.identity` from `factory.yaml` (`tobias`).

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
   executor, for example `agent-b (claude-code)`.

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
- The M1 registration `claude mcp add ... taktloop` is no longer needed; if
  you added it, remove it with `claude mcp remove taktloop` so it doesn't
  shadow `.mcp.json`.
- **Known limit (accepted for M2):** all credentials files, including
  `tobias`'s, sit in the repo's `.taktloop/credentials/`. An agent session has
  a shell, so it could run `taktloop token tobias` and act as the approver,
  which would also get around strict separation. Only the bridge skill's
  "never try another identity" stands in the way. Keeping human credentials
  away from agents (another location, another machine, or a real identity
  provider) is left for later milestones.
- `.taktloop/` is gitignored: keys, secret hashes, credentials and the database
  never land in git.

`uv run pytest` runs the tests, including end-to-end tests over a real issuer
and core.

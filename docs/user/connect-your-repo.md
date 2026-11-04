# Connect your repo

This is the **team side**: your product repository talks to a running
factory ([run-a-factory.md](run-a-factory.md)). Your sessions pull workflow
steps from the factory's core, do them with the skill each step names, and
stop at the step boundary.

## 1. Connect

In your product repository's checkout (the one the factory's
`product.checkout` points at):

```sh
taktloop connect http://127.0.0.1:8765/mcp --tickets-url http://127.0.0.1:8767/mcp
```

It does two things, and you commit both:

- copies the taktloop skills into `.claude/skills/`: `taktloop` (the bridge:
  how to pull, claim and complete a step), `taktloop-triage`,
  `taktloop-plan-review` and `taktloop-breakout` (changing the workflow);
- adds the core (and, with `--tickets-url`, the ticket stand-in) to
  `.mcp.json`, keeping any other servers there:

  ```json
  {"mcpServers": {
    "taktloop": {"type": "http", "url": "http://127.0.0.1:8765/mcp",
                 "headers": {"Authorization": "Bearer ${TAKTLOOP_TOKEN}"}},
    "tickets":  {"type": "http", "url": "http://127.0.0.1:8767/mcp",
                 "headers": {"Authorization": "Bearer ${TAKTLOOP_TICKETS_TOKEN}"}}}}
  ```

The headless runs the orchestrator starts (triage, code review) use the same
`.mcp.json` and skills, so connect before the first task. The `verify` gate
needs a GitHub Actions workflow at `.github/workflows/ci.yml` in your repo
that runs on pushes to the task branches (`taktloop/<id>-<name>`). Run `connect` again
after updating taktloop to refresh the skills.

## 2. Start a session

Every session runs as one identity with its roles (`dev-issuer.yaml` in the
factory directory):

```sh
TAKTLOOP_TOKEN=$(taktloop token agent-a --config ../taktloop-factory/factory.yaml) claude
```

Then say **work on taktloop**. The session lists the open steps your roles
allow, claims one, runs it, completes it with its artifacts (commits) and
stops. Start the next step in a new session; with `separation: strict`,
review and approve with another identity than the one that did the work.

| Identity | Roles | Use it for |
| --- | --- | --- |
| `agent-a`, `agent-b` | planner, builder, reviewer | design, plan, plan review, workspace, build, integrate |
| `tobias` | requester, approver, reviewer, factory owner | the CLI: tasks, gates, workflow approval |
| `assistant` | factory assistant | changing the workflow (`taktloop-breakout`) |
| `triage-bot`, `reviewer-bot`, `orchestrator` | – | used by the factory itself |

Accept the project's `.mcp.json` servers when Claude Code asks. Tokens last 12
hours. To read tickets in a session too, also set `TAKTLOOP_TICKETS_TOKEN`
(from the factory's `.env`). Without it, Claude Code warns about a missing
environment variable for the `tickets` server; that is expected and harmless.

## 3. When it doesn't connect

- **Calls fail with 401**: the token is missing or expired. Restart the
  session with a fresh token as above.
- **`/mcp` shows taktloop as "needs authentication"** (or "SDK auth failed"):
  Claude Code connected without the header and fell back to browser OAuth,
  which the dev issuer doesn't offer. Check: an older registration shadows
  `.mcp.json` (`claude mcp get taktloop`; remove it with `claude mcp remove
  taktloop -s local`); `TAKTLOOP_TOKEN` was empty (the `taktloop token`
  command failed); the token expired.
- **The taktloop tools are missing**: check `.mcp.json` and that the factory
  is up (`taktloop status` in the factory directory).

## 4. Other harnesses

Any harness with MCP over HTTP and a bearer token works; the skills are plain
Markdown. Codex CLI (`~/.codex/config.toml`):

```toml
[mcp_servers.taktloop]
url = "http://127.0.0.1:8765/mcp"
bearer_token_env_var = "TAKTLOOP_TOKEN"
```

Start it the same way: `TAKTLOOP_TOKEN=$(taktloop token agent-b --config
../taktloop-factory/factory.yaml) codex`.

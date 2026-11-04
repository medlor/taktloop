# ADR 0002: External tools are used through their own MCP servers; local services are MVP stand-ins

- Status: Proposed
- Date: 2026-10-31
- Deciders: @Tobias
- Refines: [ADR 0001](0001-agent-driven-orchestration-via-mcp.md), decision 4

> Text marked **[proposal]** is a suggestion that hasn't been agreed yet. **[open]** marks a question still to answer.

## Context

ADR 0001 makes an AI agent the orchestrator. It acts only through scoped MCP tools, and taktloop ships its own minimal local MCP services (for example a ticket store).

That raises the question of how a factory works with external tools its owner chooses or is required to use, such as Jira. The options were:

1. **MVP stand-in:** local minimal services exist only until real tools are connected.
2. **Permanent default with pluggable backends:** taktloop's minimal service stays the agent's only interface, and external tools are plugged in behind it.
3. **Full replacement:** taktloop's own services replace external tools.

## Decision

1. **Local minimal services are an MVP stand-in.** They let the first factory run self-contained, without any external accounts.
2. **They become permanent defaults only when that's nearly free.** A local service stays as a long-term default only if a lightweight existing solution provides it and the effort to offer it is very small. taktloop doesn't put external backends behind its own service interface.
3. **taktloop doesn't replace external tools.** It doesn't grow its own tracker, CI or code hosting.
4. **External tools are used directly, through their own MCP server plus a skill.** If a factory uses Jira, it is expected that an MCP server for Jira exists. That server is not provided by taktloop; it comes from the vendor or the community. The factory adds it to its configuration together with a **skill** that tells the agent how to use Jira for this factory's workflow. The agent then talks to Jira directly through that MCP server.
5. **No service layer in between.** taktloop doesn't wrap, proxy or normalize external tools.

So a "plugin" (PRD §3) is, at its core, **an MCP server plus a skill**, scoped to the workflow steps where the agent needs it.

## Guardrails: what stays inside taktloop

ADR 0001 says the tools enforce the hard gates. With external tools reached directly, that only holds if a gate never depends on the external tool's own state.

**Decided:**

- **taktloop's own core MCP server is not a stand-in.** It holds each task's workflow state and enforces the gates (for example `advance_task`). It always belongs to taktloop, in the MVP and later.
- **External tools are tools, not the source of truth.** Each task's state and gates live inside taktloop. Jira and similar tools only mirror that state: moving a Jira ticket to "Done" doesn't move a taktloop task.

**Decided: gate evidence must not rest on the agent's word.** Hard gates are verified by plain code through the tool's MCP server.

*The problem.* Take the gate "deploy only when CI is green". If the agent calls `advance_task(to: deploying, ci_green: true)` and the gate trusts that argument, the gate is only as safe as the agent. An agent manipulated by a prompt injection (for example in a PR comment) can simply claim CI is green.

*The fix.* MCP servers can be called by plain code, not only by an LLM. So the gate itself calls the CI system's MCP server and checks the answer deterministically:

1. The agent calls `advance_task(to: deploying, evidence: {ci_run: "12345"})`. It names *which* CI run, but doesn't state the result.
2. taktloop's gate code, with no model involved, calls the CI MCP server's tool, for example `get_workflow_run(run_id: 12345)`.
3. The gate code checks the structured result against a declared rule, for example `conclusion == "success"` and the run's commit is the task's current commit.
4. The transition is applied only if the check passes.

A gate check is declared per factory as data, for example:

```yaml
gate: ci_green
check:
  mcp_server: github
  tool: get_workflow_run
  args: {run_id: "{{evidence.ci_run}}"}
  expect:
    conclusion: success
    head_sha: "{{task.commit_sha}}"
```

*Caveats.* The exact format of a gate check (the YAML above) is still a sketch. This needs MCP tools with structured, machine-readable results (MCP `structuredContent`). Tools that only return prose can't be checked reliably. Other ways to produce trustworthy evidence:

- a **verified event** (for example a signed CI webhook) recorded in taktloop, which the gate reads; or
- a **human approval** given in taktloop itself.

Gates that don't need this strength can be marked soft and accept the agent's judgment.

## Consequences

**Positive**

- Minimal own code: taktloop maintains neither integrations nor adapters for external tools.
- Fits the "factory owner picks the tools" principle: adding a tool means adding an existing MCP server and writing a skill.
- Skills are prompts, so AI can write and adapt them quickly in the breakout chat.
- The MVP needs no external accounts.

**Negative / risks**

- **Depends on MCP servers existing and being good enough.** A tool without a usable MCP server can't be added. See "Missing MCP servers" below.
- **Larger tool surface.** External MCP servers often expose many tools, including destructive ones. Exposing only the tools a step needs (tool allowlists per step) and scoped credentials become important.
- **Less uniformity.** Two factories using different trackers have different tools and skills. The workflow can't assume identical operations, so skills carry the difference.
- **Third-party trust.** External MCP servers run with real credentials and return untrusted content (prompt-injection risk). They're operated by third parties, so taktloop depends on their security and their auth model.
- **Switching from MVP stand-in to an external tool changes the agent's tools.** Skills and tool scoping have to be adapted when a factory moves off a stand-in.

## Authentication to external MCP servers

**Decided:**

- **External MCP servers run elsewhere and are operated by a third party** (the vendor, the community, or the organization's own platform team). taktloop doesn't run or host them.
- **taktloop adheres to their authentication and authorization.** Whatever a server requires (OAuth flow, API token, required scopes such as GitHub's `repo` or `actions:read`) is dictated by that server, not by taktloop. taktloop doesn't wrap or translate it.
- **The details live in the plugin's skill.** The skill that comes with an MCP server documents what it needs: which credential, which scopes per role, and how the executor obtains it. That skill is part of the shareable template, so a factory built from a template knows exactly what to request from its identity provider or the tool's admin.
- **Credentials are held by whoever calls the server.** An executor uses its own credential (scoped per role, see `rbac.md` §7). The core uses its own credential only for gate checks and out-of-sync checks such as reading ticket status (for example the read-only GitHub credential in the MVP). **Code steps** (workflow steps run by plain code, M3b) are called by the taktloop orchestrator, which uses its own credential (in the MVP the same read-only token, a configuration choice); the gate check on the result stays with the core and its credential.

## Tool allowlists per step

**Decided:**

- **Each step declares the tools it may use** in the factory's workflow configuration, for example:

  ```yaml
  step: integrate
  role: builder
  skill: finishing-a-development-branch
  tools: [github.create_pull_request, github.get_workflow_run]
  ```

- **The bridge skill passes the list on.** `get_step_context` returns the allowed tools, and the bridge skill tells the harness to use only those (where a harness supports it, for example Claude Code's allowed-tools setting, the executor's setup applies it).
- **Real enforcement stays with the credentials.** taktloop can't control what a harness calls directly at an external MCP server. The hard boundary is the scope of the executor's own credential for that server (see "Authentication to external MCP servers"). The allowlist keeps the agent focused and documents intent; the credential scope limits the damage.

## Missing MCP servers

**Decided:**

- **taktloop can't define, implement or run missing MCP servers.** Providing an MCP server for a tool is delivered from outside the factory.
- **Another factory may do exactly that.** A separate factory whose product *is* an MCP server is a normal use of taktloop. Its output is then used by the factory that needs it.
- **A missing tool blocks the factory.** If a step needs an MCP server that isn't available, the factory's pipeline is blocked. This has to be resolved before the factory can be staged, i.e. move towards live in the factory lifecycle (PRD §3; post-MVP. In the MVP the check runs when the factory starts).

**[proposal]** taktloop checks this up front: before a factory is staged, it verifies that every MCP server its steps and gates refer to is configured and reachable, and that the tools named in code steps and gate checks exist. If an MCP server becomes unavailable while the factory is running, the affected steps are blocked and the taktloop orchestrator escalates to the factory owner.

## As built in the MVP (M4)

- **The ticket stand-in is its own process**: `taktloop tickets serve`, a small MCP server with its own port (8767), its own SQLite file and its own bearer token (`TAKTLOOP_TICKETS_TOKEN`). Its tools are shaped after Jira (`create_ticket`, `get_ticket`, `update_status`, `close_ticket`, ...). The orchestrator opens a task's ticket in the `open_ticket` code step and keeps its status in line through hooks (ADR 0004); the core never writes to it.
- **The core's out-of-sync check**: a `sync:` block in `factory.yaml` declares a read (`get_ticket`) and the expected answer (`closed: false`). The orchestrator asks the core (`check_sync`) for every active task at most every `every_minutes`; the core makes the call with its own credential and records `out_of_sync` (and later `in_sync`) once. Moving the ticket never moves the task: only the factory owner decides. In the MVP the core's tickets credential is the same token everyone uses, like the shared GitHub token.
- **The availability check is implemented**: the core (at startup, and before approving a workflow change) and the orchestrator check that every MCP server the workflow, the sync checks and the hooks name is reachable and has the named tools. While a server is missing, `create_task` is refused naming it, and `taktloop status` shows it.

## Alternatives considered

- **Permanent default with pluggable backends** (option 2). One stable interface for the agent, but taktloop would have to write and maintain an adapter for every tool. Rejected, unless a lightweight solution makes it nearly free.
- **Full replacement** (option 3). Contradicts the lean, no-rebuild principle. Rejected.

## Open

- [x] What to do for a tool without a usable MCP server: it's delivered from outside (possibly by another factory); until then the factory is blocked.
- [x] Where external MCP servers run, and how their credentials are scoped: run and operated by third parties; taktloop follows their auth; the required credentials and scopes are documented in the plugin's skill, which ships with the template.
- [x] How tool allowlists per workflow step are expressed: a `tools` list on each step, passed to the harness by the bridge skill; enforced by credential scopes, not by taktloop.

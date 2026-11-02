# taktloop M2: roles, tokens and plan review

Status: draft · Nov 2, 2026 · @Tobias

> Calls marked **[builder call]** were made while writing this design, not agreed with @Tobias. Check them while reading.

Builds on `docs/superpowers/specs/2026-11-01-mvp-milestones-and-m1-design.md` (M1, implemented on `beta`), `docs/rbac.md`, ADR 0003 and `docs/mvp-workflow.md` §2.1. Where this document is silent, M1's design still holds.

## 1. Intent

M2 makes the PRD's problem 2.5 (unclear authority) tangible. Today every caller of the core is the trusted `local` identity with every role. After M2, every caller has a **named identity** with a **fixed set of roles**, every core call is authorized from a token and audited, and a plan is **reviewed by an agent in the reviewer role** before the task finishes. @Tobias can review plans himself and can override any plan review verdict as approver.

As in M1, the milestone is a prototype @Tobias tries end to end on this repository (§9), not a component on its own.

## 2. Goals, non-goals, anti-goals

**Goals**

- Every core MCP call carries a bearer token; the core validates it like any external identity provider's token and authorizes the operation from the token's roles.
- Identities are named and pre-defined (for example `tobias`, `agent-a`, `agent-b`), each with a list of roles. An executor keeps its identity's credentials locally and swaps them for a token when a session starts. Interactive or headless makes no difference.
- The same start pattern works for Claude Code, Codex CLI and Pi: `TAKTLOOP_TOKEN=$(taktloop token <identity>) <harness>`.
- A `plan_review` step after `plan`: an agent (or @Tobias) in the reviewer role returns *pass* or *issues*; issues reopen `plan` with the findings; after a configurable number of failed reviews the task escalates to @Tobias.
- @Tobias, as approver, can override a plan review at any point and approve the plan.
- Separation of duties is a factory setting, **off by default**. In `strict` mode the core refuses self-review (§6.4).
- Every call is audited with identity, roles and executor, including refused calls.

**Non-goals for M2**

- Production identity providers (Entra ID, Okta, Keycloak), group-claim mapping, tenants, factory groups (post-MVP per `rbac.md`). M2 only guarantees that swapping the issuer is a configuration change.
- Browser-based OAuth login and token refresh. A token lives for the session; when it expires, the session is restarted.
- Claude Code `headersHelper` (Claude Code only; dropped for portability).
- Workspace, build, review, verify, integrate, code steps, gate checks via external MCP servers, the orchestrator and lease expiry enforcement (M3); triage, ticket stand-in, binding switches (M4).
- Separation rules for roles M2 doesn't use yet (builder ≠ reviewer etc.) beyond what §6.4 lists.

**Anti-goals** (failure even if it technically works)

- Issuer code inside the core, or a special path in the core for dev tokens (ADR 0003 §6).
- An unauthenticated or "trust everyone" mode left in the core.
- Separation rules expressed as conditions in the workflow YAML. The YAML only switches a fixed rule set on (`separation: strict`); the rules live in code.
- An override that rewrites or hides the reviewer's verdict. Both stay in the history.
- Exceeding the lightweight budget: **≤ ~2,200 lines** of non-test code in total (core + CLI + dev issuer) and no new dependencies beyond those already installed through `mcp`.

## 3. Approaches considered (where the dev issuer lives)

| Option | Trade-off |
| --- | --- |
| **A. Separate server inside the taktloop package (chosen)**: `src/taktloop/issuer/`, started with `taktloop issuer` on its own port | one project, one test suite, `taktloop token` sits in the CLI; the ADR boundary is held by an import rule plus a test, and the core reaches the issuer over HTTP only; one more process to start |
| B. Standalone PEP 723 script outside the package (`tools/dev_issuer.py`) | hardest boundary (delete the file and it's gone), but outside the package's tests, and `taktloop token` would call a tool the package doesn't ship |
| C. Issuer routes mounted in the core process | one fewer process, but violates ADR 0003 ("the core contains no issuer code"); dropped |

**Chosen: A**, by @Tobias.

**How a session gets its identity.** Also decided with @Tobias, after comparing harnesses: Claude Code alone can run a command to fetch credentials (`headersHelper`). Codex CLI (`bearer_token_env_var`) and Pi (`${VAR}` in headers) can only fill the token from an environment variable, and all three support MCP OAuth with browser login (one stored login per registered server, so not one identity per session). The portable choice is a token in the environment variable `TAKTLOOP_TOKEN`, fetched by `taktloop token <identity>` at session start.

## 4. Identities, credentials and tokens

### 4.1 The model (decided with @Tobias)

- People and agents have **clear, named identities**, defined ahead of time. In production these live in the organization's identity provider; in M2 they live in the dev issuer's config.
- **An identity owns a list of roles**, for example `agent-a`: planner + reviewer.
- **The identity's credentials are stored locally**, where the executor runs.
- **At session start** the harness (through `taktloop token`) presents the credentials to the dev issuer and gets a token. **The token carries all of the identity's roles.**
- Dev tokens live **12 hours** (configurable) **[builder call]**, so a working day fits in one session. No refresh.

### 4.2 Dev issuer

A small HTTP server, `taktloop issuer [--config dev-issuer.yaml]`, default `127.0.0.1:8766` **[builder call]**. It is **off unless started** and prints a warning on start that it is for development only (ADR 0003).

Its config, `dev-issuer.yaml` at the repo root (committed; contains no secrets) **[builder call]**:

```yaml
issuer: http://127.0.0.1:8766
audience: taktloop                    # aud claim; must match the core's auth.audience
token_lifetime: 12h
state_dir: .taktloop/issuer           # signing key, secret hashes, local credential files (gitignored)
identities:
  tobias:  {scopes: [taktloop:taktloop:requester, taktloop:taktloop:approver,
                     taktloop:taktloop:reviewer, taktloop:taktloop:factory_owner]}
  agent-a: {scopes: [taktloop:taktloop:planner, taktloop:taktloop:reviewer]}
  agent-b: {scopes: [taktloop:taktloop:planner, taktloop:taktloop:reviewer]}
```

Scopes use the decided form `taktloop:<factory>:<role>` (`rbac.md` §5). The issuer is generic: it knows scopes as strings, not taktloop roles.

Endpoints (standard, nothing taktloop-specific):

| Endpoint | Purpose |
| --- | --- |
| `GET /.well-known/oauth-authorization-server` | RFC 8414 metadata: `issuer`, `token_endpoint`, `jwks_uri`, `grant_types_supported: [client_credentials]` |
| `GET /jwks.json` | public signing key |
| `POST /token` | OAuth 2.1 **client credentials** grant. `client_id` = identity name, `client_secret` from the local credentials file (form body or HTTP Basic). Returns a JWT access token with all the identity's scopes |

Token: JWT signed **ES256** **[builder call]** with claims `iss`, `sub` (identity name), `aud`, `iat`, `exp`, `scope` (space-separated), `client_id`.

**Credentials.** `taktloop issuer init` creates the signing key and one secret per identity: it stores a hash of each secret in `state_dir` and writes the client-side credentials file `.taktloop/credentials/<identity>.json` (`issuer`, `client_id`, `client_secret`), mode 0600. Re-running it adds secrets for new identities and keeps existing ones; `--rotate <identity>` replaces one **[builder call]**. On this one machine both sides live under `.taktloop/`; on another machine only the credentials file is copied there.

**Known limit, accepted for M2 by @Tobias:** all credentials files, including `tobias`'s, sit in the repo's `.taktloop/credentials/`. An agent session has a shell, so it could run `taktloop token tobias` and act as the approver, which would also get around strict separation. Only the bridge skill's "don't try another identity" stands in the way. The README states this limit. Keeping human credentials away from agents (another location, another machine, or a real identity provider) is for later milestones.

### 4.3 `taktloop token <identity>`

Reads `.taktloop/credentials/<identity>.json` (directory overridable with `--credentials DIR`), runs the client credentials grant against the issuer named in it, and prints the bare access token on stdout. Errors go to stderr with a non-zero exit, so `TAKTLOOP_TOKEN=$(taktloop token agent-b)` fails loudly.

### 4.4 Wiring per harness

The core's MCP entry reads the token from `TAKTLOOP_TOKEN`:

- **Claude Code:** a project `.mcp.json` at the repo root (committed) **[builder call]**:
  `{"mcpServers": {"taktloop": {"type": "http", "url": "http://127.0.0.1:8765/mcp", "headers": {"Authorization": "Bearer ${TAKTLOOP_TOKEN}"}}}}`. The M1 user-level registration is removed.
- **Codex CLI:** `[mcp_servers.taktloop]` with `url` and `bearer_token_env_var = "TAKTLOOP_TOKEN"`.
- **Pi:** a remote server entry with `headers: {Authorization: "Bearer ${TAKTLOOP_TOKEN}"}`.

Start a session with `TAKTLOOP_TOKEN=$(uv run taktloop token agent-a) claude`. The M2 demo uses Claude Code only; the README documents the Codex and Pi entries without demoing them **[builder call]**.

### 4.5 The CLI's own identity

The CLI is an MCP client like any other. It uses `TAKTLOOP_TOKEN` if set; otherwise `--as <identity>`; otherwise `cli.identity` from `factory.yaml` (`tobias` in this repo) **[builder call]**. Without `TAKTLOOP_TOKEN` it fetches a token itself the same way `taktloop token` does, on every invocation (no token cache in M2) **[builder call]**.

## 5. Core: authentication and authorization

### 5.1 Token validation

`factory.yaml` gains a required block; the core refuses to start without it (no unauthenticated mode):

```yaml
auth:
  issuer: http://127.0.0.1:8766       # only the URL; any OIDC/OAuth issuer works
  audience: taktloop
```

The core implements the MCP SDK's `TokenVerifier` protocol (`mcp.server.auth.provider`) and passes it to `MCPServer(token_verifier=..., auth=AuthSettings(issuer_url=..., resource_server_url=...))`, so the SDK's middleware enforces the bearer token and publishes the protected-resource metadata MCP clients expect. The verifier:

1. fetches the issuer's metadata, then its JWKS (cached, refetched on an unknown `kid`) with `pyjwt[crypto]`'s `PyJWKClient`;
2. validates signature, `iss`, `aud`, `exp`;
3. returns an `AccessToken` with `subject` = `sub` and the scopes.

`pyjwt[crypto]` is already installed through `mcp`; it becomes a **declared** direct dependency because the core imports it **[builder call]**. Direct runtime dependencies: `mcp`, `pyyaml`, `pyjwt[crypto]`.

`caller_from(ctx)` (`mcp_server.py`) builds the `Caller` from the validated token: `subject` from `sub`, `roles` = the roles of the scopes that match `taktloop:<this factory>:<role>` (other factories' scopes are ignored), `executor` from the MCP client's `clientInfo` name as in M1.

**Unauthenticated calls** (missing, invalid or expired token) are refused by the SDK's middleware with HTTP 401 before any tool runs. The core logs each one to its server log (time, reason, client address). They are **not** written to `events`, because no identity, task or operation is known **[builder call]**.

### 5.2 Permissions

Checked in `service`, on every operation, before anything else. A missing role is a `Rejected` ("requires role planner; you have reviewer") and is recorded in `events` like any other rejection.

| Operation | Allowed roles |
| --- | --- |
| `list_tasks`, `get_task`, `list_open_steps`, `get_step_context` | any taktloop role in this factory |
| `create_task` | requester |
| `claim_step`, `heartbeat`, `complete_step`, own `release_step` | the step's `role` from the workflow |
| `decide_gate` | the gate's `role` (approver) |
| `override_review` (§6.3) | approver |
| `release_step --force` | factory_owner |

`list_open_steps` lists only steps whose role the caller holds. It doesn't pre-filter by separation rules: a step the caller is excluded from in strict mode is still listed, and the claim is refused with the reason **[builder call]** (this is what the demo shows).

Claim ownership: M1's claim token stays. In addition, `heartbeat`, `complete_step` and non-forced `release_step` require that the caller's subject is the one that claimed the step.

### 5.3 Audit

`events` gains `subject`, `roles` (JSON list) and `executor` columns, replacing M1's single `caller` text **[builder call]**. Every operation writes one row, allowed or rejected (reads are recorded only when rejected, as in M1). `task show` names holders and actors as `agent-b (claude-code)`.

The schema version is bumped. There are no migrations (M1 decision): an M1 database is refused with a message to delete `.taktloop/factory.db` **[builder call]**.

## 6. Workflow and behaviour

### 6.1 Workflow file

```yaml
factory: taktloop
server: {host: 127.0.0.1, port: 8765}
database: .taktloop/factory.db
auth: {issuer: http://127.0.0.1:8766, audience: taktloop}
cli: {identity: tobias}
separation: off                      # off (default) | strict, see 6.4
workflow:
  start: design
  steps:
    design:          # as in M1
      ...
    design_approval: # as in M1
      ...
    plan:            # as in M1, but next: plan_review
      ...
      next: plan_review
    plan_review:
      kind: agent
      role: reviewer
      skill: taktloop-plan-review
      instructions: >
        Review the plan against the approved design doc. Return pass or
        issues with concrete findings. Do not edit the plan.
      review_of: plan                # the step whose latest artifact is reviewed
      on_pass: done
      on_issues: plan                # reopens plan as round n+1 with the findings
      max_issue_rounds: 2            # the 2nd 'issues' escalates instead
      on_escalate: plan_escalation
    plan_escalation:
      kind: human_gate
      role: approver
      on_approve: done               # approve the plan anyway
      on_reject: [plan, design]      # first entry is the default target
```

New in the loader (validation fails at start with a clear message):

- An `agent` step is either a **work step** (`next`, `output.artifacts`) or a **review step** (`review_of`, `on_pass`, `on_issues`, optional `max_issue_rounds` + `on_escalate`, which must come together). It has no required artifacts **[builder call]**.
- `on_reject` may be a step name or a list of them.
- All targets exist or are `done`; every step is still reachable.

Still no conditions or expressions: these are fixed fields with fixed meanings.

### 6.2 Plan review

- **Completing a review step:** `complete_step(step_id, claim_token, outcome, findings?)`, with `outcome` `pass` or `issues`; `findings` (text, required for `issues`) is stored in the step run's `evidence`. Work steps keep `outcome: completed` and artifacts, as in M1.
- **pass** → `on_pass` (here: the task is done).
- **issues** → `on_issues` opens as a new round. When this is the `max_issue_rounds`-th `issues` verdict for this review step **since the task last left its escalation gate** (or since the task started), `on_escalate` opens instead. So each time @Tobias sends the task back from the escalation gate, the planner gets a fresh set of review rounds, and the loop still can't run without him **[builder call]**.
- The reviewer reads the plan and the design doc from the pinned artifacts in its step context (`git show <commit>:<path>`).
- **Human review:** `tobias` holds the reviewer role, so he can claim `plan_review` from an interactive session started as `tobias`, or pass a verdict from the CLI: `taktloop review <task> --pass` / `--issues TEXT` (claims and completes in one call) **[builder call]**.

The review prompt ships as its own skill, `templates/mvp/skills/taktloop-plan-review/SKILL.md`, and `install-skill` installs it next to the bridge skill. It follows `mvp-workflow.md` §2.1: (1) the builder check applied to the plan ("you must implement this without talking to its author: where would you have to guess, and where would a wrong guess matter?"); (2) writing-plans' self-review checklist run independently (every design requirement has a task, no placeholders, consistent names, tests for the risky cases). Findings are a ranked list; *pass* means nothing important is left.

### 6.3 Override (decided with @Tobias)

`override_review(task_id, review_step, reason)`, approver only, CLI `taktloop override <task> [--step plan_review] --reason TEXT`. It approves the reviewed work regardless of review verdicts: the task's open run is closed as `superseded` and the review step's `on_pass` target opens. It is allowed while the task is at the review step (open or claimed: the claim is cancelled), at its escalation gate, or at any round of the reviewed step (`review_of`) after the first review verdict, however that round was reopened **[builder call]**.

What it approves is the **latest completed round** of the reviewed step, never a revision still in progress; the event pins that round's artifacts (`path@commit`) **[builder call]**.

The event records the reason and every earlier verdict of that review step in this task. Verdicts are never changed. At the escalation gate, `taktloop approve` and `override` reach the same result; both remain available **[builder call]**.

### 6.4 Separation of duties (decided with @Tobias)

`separation: off` (the default): no per-task identity checks. One identity may plan, review its own plan, revise it and review the revision. Role checks (§5.2) always apply.

`separation: strict` adds, from the core's own records:

1. A reviewer may not claim a review step if it completed **any** round of the reviewed step (`review_of`) in this task.
2. A planner may not claim a round of a reviewed step if it has completed the review step in this task.
3. An approver may not decide a gate, or override a review, if it completed any round of the step whose artifact is being approved **[builder call]**. That step is derived, with no new YAML field: for an escalation gate and for an override, the review step's `review_of`; for any other gate, the nearest work step before it (for `design_approval`, `design`). This doesn't affect @Tobias today: `tobias` has no planner role.

A refusal names the rule and the earlier event ("strict separation: agent-a completed plan round 1 of task 3").

### 6.5 Feedback per step (carried over from M1)

M1's `rejection_reason` was task-wide. `get_step_context` replaces it with `feedback`: the reason or findings that reopened **this** step's current round (a gate rejection or a review's findings), with who gave it and when; `null` in round 1. The bridge skill's revision-round instruction uses `feedback`.

### 6.6 Waiting-for

Unchanged rules from M1, plus: open review step → "waiting for a reviewer to pick up *plan_review*"; escalation gate → "waiting for approver decision at *plan_escalation* (plan failed review 2 times)".

## 7. Code shape

- **Split `service.py` first** (288/300 lines), before adding auth, in its own commit with no behaviour change. For example: `service.py` (the `_run`/audit frame and reads), `steps.py` (claim, heartbeat, complete, release), `gates.py` (gates, override) **[builder call]**.
- New core modules: `auth.py` (token verifier and scope-to-role mapping), `separation.py` (the strict rules as pure functions over records), `permissions.py` (operation → role table). Exact names are left to the builder.
- `src/taktloop/issuer/` (app, keys, config) and `taktloop token` in the CLI. **Boundary test:** no module outside `issuer/` imports from it, and `issuer/` imports nothing from the core. The core talks to the issuer over HTTP only.
- `taktloop serve` and `taktloop issuer` stay separate processes. A combined `taktloop dev` that starts both is left out of M2 **[builder call]**.
- Line budget: ≤ ~2,200 non-test lines in total; no module over ~300 lines.

## 8. Bridge skill changes

- Setup line: register the core through the project's `.mcp.json` and start sessions with `TAKTLOOP_TOKEN=$(uv run taktloop token <identity>) claude`. If tool calls fail with 401, tell the user the token is missing or expired and to restart the session that way.
- Review steps: run the skill from the context, then `complete_step` with `outcome` and `findings`, no artifacts. Never edit the reviewed artifact.
- Revision rounds read `feedback` instead of `rejection_reason`.
- A claim refused for role or separation reasons: report the reason and stop; don't try another identity.

## 9. M2 acceptance (the demo)

With the issuer and the core running, `factory.yaml` set to `separation: strict`:

1. `taktloop issuer init` creates credentials for `tobias`, `agent-a` and `agent-b`.
2. `taktloop task create "…"` (as `tobias`) → task created; `task show` names `tobias` as creator.
3. A session started as `agent-a` runs design; `taktloop approve` (as `tobias`). A call without a token (`curl` to `/mcp`) gets 401.
4. A session as `agent-a` writes the plan and completes it.
5. A session as `agent-a` sees `plan_review` and tries to claim it → refused by strict separation. A session as `agent-b` claims it and returns *issues*.
6. A session as `agent-a` gets plan round 2 with the findings as `feedback` and revises it. `agent-b` returns *issues* again → the task escalates to `plan_escalation`.
7. `taktloop override <task> --reason "…"` (as `tobias`) → done.
8. `taktloop task show` lists every event with identity, roles and executor: the refused claim, both verdicts, the escalation and the override.
9. Switch to `separation: off`, restart the core, run a new task's plan review as the planning identity → allowed. `taktloop review <task> --pass` from the CLI works as well.

Automated tests (`uv run pytest`): issuer metadata, JWKS and token grant (good and bad secrets); token verification (bad signature, wrong issuer, wrong audience, expired, other factory's scopes); the permissions table, one allowed and one refused call per operation; each strict rule and that `off` skips them; review pass, issues, escalation and override; per-step feedback; the import-boundary test; an end-to-end run with a real issuer and core over HTTP.

## 10. Constraints

- Everything from M1 §8 still holds (Python ≥ 3.12, uv only, SQLite, `src/taktloop/`, small modules, English docs, product name "taktloop").
- Direct runtime dependencies: `mcp` (≥ 2.3), `pyyaml`, `pyjwt[crypto]` (already installed through `mcp`).
- `.taktloop/` stays gitignored, so credentials, keys and secret hashes never land in git.
- The dev issuer binds to `127.0.0.1` by default and must be started explicitly.

## 11. Left to the builder

Exact module split and names; pydantic models; SQL details; JWKS caching details; CLI rendering of identities, roles and verdicts; the exact wording of both skills (within §6.2 and §8); README setup for M2, including the Codex and Pi snippets.

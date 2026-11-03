# taktloop M3b: the orchestrator — code steps, a pushed reviewer, leases and stalls

Status: draft · Nov 3, 2026 · @Tobias

> Calls marked **[builder call]** were made while writing this design, not agreed with @Tobias. Check them while reading.

Builds on the M1, M2 and M3a designs (`docs/superpowers/specs/2026-11-01-…`, `2026-11-02-m2-…`, `2026-11-02-m3a-…`), all implemented and on `main`, and on ADR 0002 and ADR 0004. Where this document is silent, those still hold.

## 1. Intent

After M3a a task goes from idea to merged code, but **every step is pulled by hand** from a session, and nothing happens unless someone touches the core: a lease that ran out is only noticed when someone loads the step, a task nobody picks up just sits there, and `verify` needs an agent to look up a CI run id.

M3b adds the **taktloop orchestrator** (ADR 0004): a separate process that makes the factory move on its own where no judgment is needed.

- It runs **code steps** itself (no LLM): `verify` becomes a code step that finds the CI run of the change.
- It **pushes** a step: when `code_review` opens, it starts a **headless Claude Code reviewer** that pulls, reviews and completes the step.
- It drives **lease expiry** on a timer instead of lazily.
- It **flags stalls**: steps nobody picks up, leases that keep expiring, code steps that never get through.

As before, the milestone is a prototype @Tobias tries end to end on this repository (§10).

## 2. Goals, non-goals, anti-goals

**Goals**

- A new long-running process, `taktloop orchestrator`, started next to `taktloop serve`. It is an **MCP client of the core with its own identity**, like any executor (approach B, §3).
- **A clear core boundary** (decided with @Tobias): the core keeps state, rules, permissions and **hard-gate checks**; it never acts on the outside world. Everything that *acts* — running code steps, starting headless runs, the timer — lives in the orchestrator.
- **Code steps** (`kind: code`): one declared MCP call made by the orchestrator with its own credential; the result becomes the step's evidence. A hard-gate `check` on the same step is still run **by the core** with the core's credential, unchanged from M3a.
- **Pushed agent steps** (`dispatch: push`): the orchestrator starts `claude -p` as the step's configured identity, **read-only tools only** (decided with @Tobias).
- Claude auth for headless runs: **the subscription login if present, else the API key** (decided with @Tobias).
- **Lease expiry on a timer** and **stall flagging**: a stalled step stays open and waiting, and is shown as *stalled* in `taktloop status` (decided with @Tobias). No gate, no automatic action.

**Non-goals for M3b**

- Notifying people (`push: {notify: …}`), AI judgment on unusual stalls (ADR 0004 allows it; not needed yet).
- Harnesses other than Claude Code for pushed runs (Codex, Pi). The start command is declared so they can be added (M4 or later).
- Code steps that write (merge, open a PR). The orchestrator's GitHub credential stays read-only.
- A limit on concurrent headless runs beyond "one per step" **[builder call]**.
- Per-step tool allowlists enforced by the core (ADR 0002), the startup availability check, the ticket stand-in, triage (M4).
- Running the orchestrator as a service (systemd, restart on crash). It runs in a terminal like `serve`.

**Anti-goals** (failure even if it technically works)

- The core trusting the orchestrator about a hard gate. A local session can mint any identity's token (M2's shared credentials dir), so a gate result reported by the orchestrator could be faked; the core asks GitHub itself.
- The orchestrator growing into a workflow engine: no branching or retry logic per step. Its rules are generic (run, retry on refusal, start, flag).
- A headless reviewer that can edit files or push.
- A headless run billed to the API key while a subscription login exists.
- Exceeding the budget: **≤ 2,900 non-test lines** under `src/` in total (raised from 2,600 by @Tobias), counted as before (`wc -l` over every `.py` file under `src/`). `src/` is at 2,472, so M3b has **~428 lines**. No new runtime dependency.

## 3. Approaches considered (where the loop lives)

| Option | Trade-off |
| --- | --- |
| A. A background thread inside `taktloop serve` | one process, fewest lines; but `serve` supervises child processes and the core acts as itself, bypassing permissions |
| **B. A separate `taktloop orchestrator` process, an MCP client with its own identity (chosen)** | its actions pass the same permission checks as everyone's; clean core boundary; costs more lines (token refresh, MCP calls) and a second process to start |
| C. Act only on transitions, no timer | covers pushes, not expiry, stalls or CI-still-running retries; would end as A plus wiring; dropped |

**Chosen: B**, by @Tobias. Follow-up decision with @Tobias: code steps run **in the orchestrator**, not the core; the hard-gate check stays in the core (§5).

## 4. The workflow change

Only two steps change in `factory.yaml` (and `templates/mvp/factory.yaml`):

```yaml
orchestrator:                          # read by `taktloop orchestrator` (§6)
  identity: orchestrator
  tick_seconds: 15                     # [builder call]
  code_retry_seconds: 60               # §5.2
  stall_after_minutes: 60              # §7
  max_lease_expiries: 2                # §7
workflow:
  steps:
    code_review:
      kind: agent
      role: reviewer
      skill: requesting-code-review
      dispatch: push                   # the orchestrator starts an executor (§6.3)
      push: {start: claude-headless, identity: reviewer-bot}
      # instructions, review_of, on_pass, on_issues, max_issue_rounds, on_escalate: as in M3a
    verify:
      kind: code                       # run by the orchestrator, no LLM (§5)
      role: verifier
      call:                            # the orchestrator's own MCP call
        mcp_server: github
        tool: actions_list
        args:
          method: list_workflow_runs
          owner: medlor
          repo: taktloop
          resource_id: ci.yml
          workflow_runs_filter: {branch: "{{artifacts.change.branch}}"}
        evidence:                      # evidence key: dotted path into the result
          ci_run: workflow_runs.workflow_runs.0.id
      evidence: [ci_run]
      check: …                         # unchanged from M3a: the core's hard gate
      next: integration_approval
      on_fail: build
```

**Why this call works** (confirmed live on Nov 3, 2026 against `medlor/taktloop`): `actions_list` / `list_workflow_runs` has **no commit filter** (an unknown `head_sha` filter is silently ignored), but `workflow_runs_filter.branch` works and runs come **newest first**; `per_page` is ignored. The result arrives as `structuredContent`: `{"method", "workflow_runs": {"total_count", "workflow_runs": [{id, head_sha, head_branch, path, status, conclusion, …}]}}`. So the step takes the **newest `ci.yml` run on the task branch**, and the core's existing check requires its `head_sha` to be the reviewed change commit and its status `completed`.

**Consequences:**

- The newest run is still going → the core refuses (`status` mismatch) → the orchestrator keeps its claim and tries again on a later tick.
- The builder pushed a newer commit after review → the newest run's `head_sha` never matches → the step keeps being refused and is flagged **stalled** after `stall_after_minutes` (§7). That is the right outcome: the reviewed commit is no longer the branch head, a person must look.
- No run yet on the branch (index 0 missing) → the code step cannot produce its evidence → treated like a refusal: retry, eventually stalled.
- The `verifier` role now belongs to the orchestrator identity only: **`agent-a` and `agent-b` lose `verifier`** in `dev-issuer.yaml` (and the template), so no session sees or claims `verify` and races the orchestrator. The core stays generic; no "only the orchestrator may claim code steps" rule **[builder call]**.

## 5. Code steps

### 5.1 Declaration

`kind: code` steps have `role`, `call`, `next`, and optionally `evidence` + `check` + `on_fail` (M3a's rules for those three, unchanged). They have no `skill`, `output`, `instructions`, review keys or `dispatch`.

`call` has `mcp_server` (a name in `mcp_servers`), `tool`, `args`, and `evidence`, a mapping from evidence key to a **dotted path** into the result. If the step declares `evidence: [keys]`, `call.evidence` must produce exactly those keys (checked at load).

**Shared changes to the check machinery** (`checks.py`):

- **`args` may be nested** (mappings, lists, scalars) for both `call` and `check`. Placeholders are substituted in every string value at any depth; non-string scalars pass through as they are. M3a's flat string args remain valid. Needed for `workflow_runs_filter`.
- **Dotted paths may index lists** with a non-negative integer segment (`workflow_runs.0.id`), in `call.evidence`, `match` and `expect`.
- Placeholders are validated at load as in M3a.

### 5.2 Running a code step (in the orchestrator)

On each tick, for every open code step whose role the orchestrator holds:

1. `claim_step`. (A code step stays **claimed** across retries; the orchestrator heartbeats it like any executor.)
2. `get_step_context` for the artifacts; resolve placeholders in `call.args` **in the orchestrator**, with the same pure function the core uses.
3. Call the tool on the external server with **the orchestrator's credential** (§6.2), reusing `external.py`'s client (timeout, error naming).
4. Read each `call.evidence` path. A missing path, a tool error or an unreachable server → nothing is completed; the reason is logged and kept for the stall rule; retry on the next tick.
5. `complete_step(step_id, claim_token, evidence={…})`. The core runs the check as in M3a: **refused** → keep the claim, retry later; **failed** → `on_fail` opens; **passed** → `next` opens.

A code step that did not get through is retried **at most once every 60 s** (`code_retry_seconds: 60` in the `orchestrator:` block), not every tick, so a 10-minute CI run adds ~10 refused attempts to the history, not ~80, and GitHub sees ~120 calls an hour per waiting step **[builder call]**. Code steps keep being retried even after they are flagged stalled: that is harmless and lets `verify` recover by itself (decided with @Tobias).

The orchestrator logs one line per attempt; the core's history already records each `complete_step` (allowed/refused with reason) and each `check` event.

### 5.3 What stays in the core

`complete_step` on a step with a `check` works exactly as in M3a: the core calls GitHub with **its own** credential and decides. The only core changes for code steps are: the workflow loader accepts `kind: code`, `list_open_steps` includes open code steps for callers holding the role, and the `waiting_for` text ("waiting for the orchestrator to run *verify*" **[builder call]**).

## 6. The orchestrator process

### 6.1 Start and identity

`taktloop orchestrator [--config factory.yaml]` runs in the foreground until Ctrl-C, logging one line per action to stdout.

- It gets its token with `fetch_token(<orchestrator.identity>, <credentials dir>)` (M2's client-credentials flow) and fetches a new one when the core answers 401 or the token is within 5 minutes of expiry **[builder call]**.
- New identities in `dev-issuer.yaml` (created by `taktloop issuer init` like the others):

```yaml
orchestrator: {scopes: [taktloop:taktloop:orchestrator, taktloop:taktloop:verifier]}
reviewer-bot: {scopes: [taktloop:taktloop:reviewer]}
```

- New role `orchestrator` for two new core tools (§6.4, §7). The orchestrator holds no other work role than the code steps' roles (`verifier` here), so it cannot plan, build, review or decide gates.

### 6.2 Its GitHub credential

The orchestrator reads `mcp_servers` from the same `factory.yaml` and resolves the credential the same way as the core (environment, then `.env` next to `factory.yaml`). For now it is the **same read-only PAT** as the core's (agreed with @Tobias): ADR 0002 says each caller uses its own credential, and a separate token is a configuration change, not a code change. Known limit.

### 6.3 Pushed agent steps

A step with `dispatch: push` and `push: {start: claude-headless, identity: <id>}` (agent steps only, review steps included). `start` accepts only `claude-headless` in M3b; anything else is refused at load.

On each tick, for every **open and unclaimed** pushed step whose push has no live child process of this orchestrator:

1. Check Claude's auth with `claude auth status` (JSON). If `loggedIn` is true and `authMethod` is `claude.ai`, the child's environment **drops `ANTHROPIC_API_KEY`** (Claude Code prefers a set key over the subscription). Otherwise the child keeps `ANTHROPIC_API_KEY` if set; if neither exists, the step is not started and the reason is logged (and counts for the stall rule).
2. Fetch a token for `<id>` and set `TAKTLOOP_TOKEN` in the child's environment.
3. Start in the factory directory (the one holding `factory.yaml`):

   ```
   claude -p "work on taktloop step <step_id>" --allowedTools <read-only list> --disallowedTools <write list>
   ```

   **Read-only list** (decided with @Tobias: read-only for now): `Read`, `Grep`, `Glob`, `Skill`, `Bash(git diff:*)`, `Bash(git log:*)`, `Bash(git show:*)`, `Bash(git rev-parse:*)`, the taktloop MCP tools (`mcp__taktloop__*`). **Disallowed:** `Edit`, `Write`, `NotebookEdit`, and Bash beyond the list. The exact flags (and whether `Agent`, used by the review skill to dispatch a reviewer subagent, inherits the restrictions) are checked by the builder against `claude --help` and the demo **[builder call]**. The run must not inherit a `bypassPermissions` mode from local settings: pass an explicit `--permission-mode` that denies unlisted tools.
4. stdout and stderr go to `.taktloop/runs/<step_id>-<n>.log` (`.taktloop/` is gitignored). The core is not told about the log; `taktloop status` does not show it **[builder call]**, the orchestrator's own log line names it.

**Failed starts** (decided with @Tobias): a start counts as failed when it could not be launched (no auth, no token for the identity, `claude` missing) or the child exited without the step having been claimed by `<id>`. The orchestrator counts failed starts per step run in memory. They feed stall rule 2 (§7), and **once a pushed step is stalled, the orchestrator starts no more runs for it** for the rest of its process life; restarting the orchestrator (after fixing the cause) tries again. This caps paid runs on a broken push at `max_lease_expiries`.

**No duplicates, no bookkeeping in the core:** a running reviewer holds the claim, so the step is no longer "open and unclaimed". After an orchestrator restart, a still-running child keeps its claim; a dead one's lease runs out (§7) and the step is pushed again. A child that runs longer than the step's lease without completing is killed by the orchestrator **[builder call]**.

The headless reviewer reads the change from git objects (`git diff <workspace.commit>..<change.commit>`); no checkout of the task branch is needed, since branches were pushed and the factory directory is the same repository. If the commit is not present locally, the orchestrator runs `git fetch origin` before starting the child **[builder call]**.

**Bridge skill:** when the prompt names a step id, claim that step instead of listing; never ask the user anything in a headless run. One short paragraph in `templates/mvp/skills/taktloop/SKILL.md`.

### 6.4 Lease expiry on a timer

New core tool **`sweep()`**, role `orchestrator`. In one transaction it:

- expires every claimed step run whose lease has passed (the same `lease_expired` event as today's lazy path, by `core`);
- returns every not-closed step run: `step_id`, `task_id`, `step`, `kind`, `dispatch`, `role`, `status`, `claimed_by`, `idle_since` (when it was opened or last became open again), `lease_expiries` (count of `lease_expired` events on this run), `stalled` (§7).

The orchestrator calls `sweep` first on every tick and drives everything else from its result. The lazy expiry path stays for operations that load a run.

## 7. Stalls

The orchestrator flags a step run as stalled when any of these holds **[builder call on the exact rules]**:

1. **Nobody picks it up:** it is open and unclaimed, and `idle_since` is more than `stall_after_minutes` ago. Human gates count (a task waiting on @Tobias for an hour is flagged).
2. **Leases keep expiring or pushes keep failing:** `lease_expiries` + failed starts of a pushed step (§6.3) `≥ max_lease_expiries` (catches a headless reviewer that dies every time, before or after claiming) (decided with @Tobias).
3. **A code step never gets through:** the orchestrator has been retrying it (refused, missing evidence path, server error) for more than `stall_after_minutes`, measured from its first attempt on this step run in the current orchestrator process.

It then calls the new core tool **`report_stall(step_id, reason)`**, role `orchestrator`. The core:

- records an **`escalate` event** by the orchestrator with `{"kind": "stall", "reason": …}` — **once per step run**; a second report is accepted and ignored **[builder call]**;
- changes nothing else: the step stays open (or claimed) and waiting (decided with @Tobias).

`waiting_for` appends ` — stalled: <reason>` while the run is not closed, for example "waiting for an approver decision at *integration_approval* — stalled: nobody picked it up for 60 min". `taktloop status` and `task show` show it; `task show` renders the stall event in one line.

## 8. Docs to update

- **ADR 0002:** "The core uses its own credential only for gate checks, code steps and out-of-sync checks" → code steps use the orchestrator's credential; gate checks stay with the core.
- **ADR 0004:** the taktloop orchestrator is a separate process acting through the core's MCP tools, not a component inside the core.
- **README:** how to start the orchestrator, the two new identities, the headless auth rule.

## 9. Code shape and budget

| Part | Where | ~Lines |
| --- | --- | --- |
| `kind: code`, `call`, `dispatch`/`push`, nested args, list-index paths | `workflow.py`, `checks.py` | 60 |
| `orchestrator:` config block | `config.py` | 15 |
| `sweep`, `report_stall`, permissions, stalled `waiting_for` | new `ops_orchestrator.py`, `mcp_server.py`, `permissions.py`, `rules.py` | 90 |
| Orchestrator loop, MCP client calls, token refresh | new `orchestrator.py` | 130 |
| Headless start: auth check, env, command, logs, kill | new `headless.py` | 70 |
| CLI command, rendering of stall events | `cli.py`, `cli_render.py` | 25 |
| **Total** | | **~390** of 428 |

No module over ~300 lines. The plan carries a line allowance per task and a running count.

## 10. M3b acceptance (the demo)

With the issuer, the core and the orchestrator running, `separation: strict`, on a small real change chosen by @Tobias:

1. A task goes through design, plan, review, workspace and build in sessions, as in M3a.
2. When `build` completes, **without anyone pulling it**, the orchestrator starts a headless reviewer as `reviewer-bot`; it claims `code_review`, reviews read-only and completes with pass or issues. `task show` shows `reviewer-bot` and the run log is in `.taktloop/runs/`.
3. `verify` runs on its own: while CI is running, the history shows refused completions (`status is in_progress`); once green, the core's `check` event passes and `integration_approval` opens. A deliberate red run sends the task back to `build` as in M3a.
4. A session claims a step and is closed. Within a tick after the lease ends, `task show` shows `lease_expired` and the step is open again — nobody touched the core.
5. `integration_approval` is left alone longer than `stall_after_minutes` (lowered for the demo, e.g. 2 min): `taktloop status` shows it as *stalled*, one `escalate` event is in the history.
6. Breaking the headless start (e.g. a `push.identity` without the `reviewer` role) leads to *stalled* via rule 2 after two failed starts, and no third run is started.
7. Approve, integrate in a session; the task is done.

**Automated tests:** workflow loading of `kind: code`, `call`, `dispatch`/`push` and their refusals; nested args and list-index paths; `sweep` (expiry, counts, idle_since) and `report_stall` (once, permissions); stalled `waiting_for`; the orchestrator tick against a real core with a fake external MCP server (code step refused → retried → passes; push started once, not twice; stall reported once; failed starts counted, no start after a stall; code-step retry interval), with the Claude start replaced by a fake command; the auth decision (subscription present → key dropped; absent → key kept; neither → not started). The live GitHub test gains one `list_workflow_runs` call with the branch filter.

## 11. Constraints

- Everything from M1 §8, M2 §10 and M3a §10 still holds.
- Direct runtime dependencies unchanged: `mcp`, `pyyaml`, `pyjwt[crypto]`. Child processes via the standard library.
- **Claude Code CLI** on `PATH` (2.1.316 here); `claude auth status` prints JSON with `loggedIn` and `authMethod` (`claude.ai` for the subscription), confirmed Nov 3, 2026.
- Neither the GitHub token nor any taktloop token is written to logs, events or run logs.

## 12. Left to the builder

Exact module names and split; pydantic models of `sweep`; log line format; the exact `claude` flags for the read-only allowlist (within §6.3); how the orchestrator tracks its children; the bridge skill's wording (within §6.3); the README update.

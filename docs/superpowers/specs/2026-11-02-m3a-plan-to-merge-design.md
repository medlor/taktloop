# taktloop M3a: plan to merge, with a hard CI gate

Status: draft · Nov 2, 2026 · @Tobias

> Calls marked **[builder call]** were made while writing this design, not agreed with @Tobias. Check them while reading.

Builds on `docs/superpowers/specs/2026-11-01-mvp-milestones-and-m1-design.md` (M1) and `docs/superpowers/specs/2026-11-02-m2-roles-and-separation-design.md` (M2), both implemented and merged to `main`. Also on `docs/mvp-workflow.md` §2 and ADR 0002 (gate checks). Where this document is silent, M1's and M2's designs still hold.

## 1. Intent

M3 makes the PRD's problems 2.5 (hard gates) and 2.4 (one source of truth across a *full* lifecycle) tangible. Today a task ends once its plan passes review. After M3a, the same task continues **from the reviewed plan to code merged on `main`**, and one transition, *verified → ready to integrate*, is a **hard gate**: the core itself asks GitHub, through GitHub's own MCP server, whether CI is green on exactly the commit that was reviewed. The agent only names the CI run; it never states the result (ADR 0002).

As in M1 and M2, the milestone is a prototype @Tobias tries end to end on this repository (§9), not a component on its own.

**M3 is split (decided with @Tobias):**

- **M3a (this document):** workspace, build, code review, verify, integrate; the CI-green hard gate via GitHub's MCP server; a real GitHub Actions workflow. Every step is still pulled by hand from a session.
- **M3b (its own design round, after M3a is tried):** code steps, the first pushed step (a headless reviewer started by the core), a background loop in the core, lease expiry enforcement and stall detection.

## 2. Goals, non-goals, anti-goals

**Goals**

- After `plan_review` passes, a task goes on through **workspace → build → code review → verify → integration approval → integrate → done**, each step pulled by a session in the right role, across as many sessions and waits as needed.
- **CI-green hard gate:** completing `verify` triggers a check the core runs itself: it calls GitHub's MCP server with its own read-only credential, and only advances if the named run is finished, belongs to this repository's latest reviewed commit, and succeeded.
- A **red CI run** sends the task back to `build`, with the failed run as `feedback`. A run that is still going, or that belongs to another commit, is **refused** without changing anything.
- The gate check is **declared as data** in `factory.yaml` (chosen with @Tobias, approach A in §3), so M3b's code steps and M4's templates reuse the same mechanism.
- A real **GitHub Actions workflow** runs the tests on every push, so the gate has something real to check.
- Code review reuses M2's review step: *issues* reopen `build` with the findings; after 2 failed rounds the task escalates to @Tobias.

**Non-goals for M3a**

- Code steps, pushed steps, starting headless runs, a background loop, lease expiry enforcement, stall detection (M3b).
- Pull requests, branch protection, "PR with approval" enforcement (later, decided with @Tobias: for now integrate merges and pushes `main` directly).
- Integration options other than merge (open a PR, keep the branch, discard the task). Not deciding is "keep"; discarding is left out **[builder call]**.
- Workspaces other than a branch (cloud VM, scaffolding). @Tobias: a branch is enough for the MVP.
- Per-step tool allowlists (ADR 0002), the startup availability check of external MCP servers (M4), the ticket stand-in, triage (M4).
- Verifying the integrate step's merge on GitHub (see §6.6, known limit).

**Anti-goals** (failure even if it technically works)

- The gate trusting anything the agent says about the CI result. The agent supplies a run id only.
- A GitHub-specific gate in Python (approach B, rejected) or direct GitHub REST calls (approach C, rejected by ADR 0002).
- A check language that grows into an expression engine: no conditions, no operators beyond "equals", no loops.
- Holding a SQLite write transaction while waiting on the network.
- Exceeding the budget: **≤ 2,600 non-test lines in total** after M3a and M3b (decided with @Tobias), counted like M2's budget: `wc -l` over every `.py` file under `src/`, blank lines and docstrings included. `src/` is at 2,150 today, so M3a and M3b add ~450 between them. M3a aims for **≤ ~2,380** (about +230), leaving ~220 lines for M3b **[builder call]**. The plan carries a line allowance per task and a running count. No new runtime dependency.

## 3. Approaches considered (how the core checks CI)

| Option | Trade-off |
| --- | --- |
| **A. Core calls GitHub's hosted MCP server; the check is declared in `factory.yaml` (chosen)** | follows ADR 0002 and the PRD; the `mcp` SDK already has an HTTP client, so no new dependency; reusable by M3b code steps and M4 templates; depends on the GitHub MCP server's tool names and result shape, which may arrive as JSON text rather than `structuredContent` |
| B. Same MCP call, but the CI check is hard-coded in Python | ~40 lines fewer, but breaks "the workflow is data"; M4 would rewrite it |
| C. Core calls GitHub's REST API directly | simplest and sturdiest, but an adapter, which ADR 0002 rules out; dropped |

**Chosen: A**, by @Tobias.

**Also decided with @Tobias:**

- Work on **this repository** (taktloop building itself). No separate factory repository for M3a. `medlor/taktloop-factory-example` stays an option for later.
- `main` and `origin` are used for real: task branches are pushed to `origin`, and integrate merges into `main` and pushes it.
- M3a + M3b together stay within **2,600** non-test lines.

## 4. The workflow

`factory.yaml` (and `templates/mvp/factory.yaml`) continue after `plan_review`:

```yaml
mcp_servers:                       # external MCP servers the core itself calls (§5)
  github:
    url: https://api.githubcopilot.com/mcp/x/actions/readonly
    token_env: TAKTLOOP_GITHUB_TOKEN
workflow:
  start: design
  steps:
    # design, design_approval, plan: as in M2
    plan_review:
      ...                                    # as in M2, but:
      on_pass: workspace
    plan_escalation:
      ...                                    # as in M2, but:
      on_approve: workspace
    workspace:
      kind: agent
      role: builder
      skill: using-git-worktrees
      instructions: >
        Create the task branch taktloop/<task id>-<short-name> from origin/main,
        verify a clean test baseline, push the branch to origin.
      output: {artifacts: [workspace]}       # path ".", commit = the base commit
      next: build
    build:
      kind: agent
      role: builder
      skill: subagent-driven-development
      instructions: >
        Implement the approved plan on the task branch with test-driven development.
        Commit, push the branch to origin. Do not merge and do not open a pull request.
      output: {artifacts: [change]}          # path ".", commit = the pushed branch head
      next: code_review
    code_review:
      kind: agent
      role: reviewer
      skill: requesting-code-review
      instructions: >
        Review the change (workspace commit .. change commit) against the plan.
        Return pass, or issues with the critical and important findings. Do not edit code.
      review_of: build
      on_pass: verify
      on_issues: build
      max_issue_rounds: 2
      on_escalate: build_escalation
    build_escalation:
      kind: human_gate
      role: approver
      on_approve: verify                     # accept the change despite the review
      on_reject: [build, plan]
    verify:
      kind: agent
      role: verifier
      skill: verification-before-completion
      instructions: >
        Run the tests locally on the change commit. Wait for the GitHub Actions run
        of that commit to finish, then complete with its run id as evidence.
      evidence: [ci_run]                     # required evidence keys (§6.3)
      check:                                 # the hard gate (§5)
        mcp_server: github
        tool: actions_get
        args:
          method: get_workflow_run
          owner: medlor
          repo: taktloop
          resource_id: "{{evidence.ci_run}}"
        match:                               # else: refused, nothing changes
          workflow_run.head_sha: "{{artifacts.change.commit}}"
          workflow_run.path: .github/workflows/ci.yml
          workflow_run.status: completed
        expect:                              # else: back to on_fail with the result
          workflow_run.conclusion: success
      next: integration_approval
      on_fail: build
    integration_approval:
      kind: human_gate
      role: approver
      on_approve: integrate
      on_reject: [build, plan]
    integrate:
      kind: agent
      role: builder
      skill: finishing-a-development-branch
      instructions: >
        Merge the change commit (not the branch tip) into main, push main to origin.
        Do not ask which option to take; the approver decided "merge".
      output: {artifacts: [merge]}           # path ".", commit = the merge commit on main
      next: done
```

Notes:

- **Artifacts without a file.** `workspace`, `change` and `merge` point at a commit, not a document, so their `path` is `"."` (already valid under M1's rules) **[builder call]**.
- **The change under review** is `workspace.commit .. change.commit` on the change's branch. Each build round produces a new `change` artifact; the check always compares against the **latest round's** `change.commit`, which is the commit the review passed.
- **Field names in `match`/`expect` were confirmed live** (§10): the server nests the run under `workflow_run`, hence dotted paths.
- **`workflow_run.path` pins the workflow file**, so a builder can't add an always-green workflow to the branch and name its run. The workflow file itself is still on the builder's branch: a change to `.github/workflows/ci.yml` that weakens the tests passes the gate. Code review is the guard against that in M3a; protected CI configuration is for later, with branch protection (known limit).
- **Execution mode** for build is subagent-driven (the skill named in the step); the bridge skill already stops skills from asking.
- **Review rounds per cycle** **[builder call]**: M2 counts `issues` verdicts since the task last left the escalation gate. In M3a `build` also reopens through a red CI run or an `integration_approval` rejection, after a review had passed. The count therefore restarts after the review step's last **`pass`** as well: `max_issue_rounds` limits failed rounds per review cycle, not per task. This changes `_after_issues` slightly and applies to `plan_review` too (only reachable there once a later step sends the task back to `plan`).
- **Why review goes again after a red CI run:** `on_fail: build` reopens build, whose `next` is `code_review`, so every change that reaches `verify` has been reviewed at the exact commit CI checks. This is intended.

## 5. Gate checks against external MCP servers

### 5.1 Configuration

`mcp_servers` in `factory.yaml` names each external server the core calls itself, with its URL and the **name of the environment variable** holding the core's credential (never the credential itself). The core sends it as `Authorization: Bearer <token>`. For M3a there is one: GitHub's hosted MCP server, restricted to the Actions toolset, read-only, through its URL.

**The core's GitHub credential:** a fine-grained personal access token for `medlor/taktloop` only, with **Actions: read** and **Metadata: read**, nothing else. It sits in a **`.env` file next to `factory.yaml`** (decided with @Tobias; `.env` is already gitignored) as `TAKTLOOP_GITHUB_TOKEN=…`. The core reads the variable from its environment first, then from that `.env` (plain `KEY=VALUE` lines, `#` comments; no new dependency) **[builder call on the precedence and format]**. Agents never see it: executors use their own credentials (`gh` login) for pushing and looking up run ids.

**Loading rules:** a step's `check.mcp_server` must name a server in `mcp_servers`, otherwise the config is refused at start. A missing environment variable is reported at start as a warning, and a check run without it is refused with "the core has no credential for github (TAKTLOOP_GITHUB_TOKEN is not set)" **[builder call]**. Checking that the server is reachable at start is M4's availability check.

### 5.2 The check declaration

A step may carry one `check`, with fixed fields:

| Field | Meaning |
| --- | --- |
| `mcp_server` | a name from `mcp_servers` |
| `tool` | the tool to call |
| `args` | the tool's arguments; string values may contain placeholders |
| `match` | fields that must equal the given values for the evidence to be **the right thing** (right commit, finished). If not: the completion is **refused**, nothing changes, the agent can call again later |
| `expect` | fields that must equal the given values for the gate to **pass**. If not: the step closes as `failed` and `on_fail` opens |

Placeholders, the complete list: `{{evidence.<key>}}` (from the completion), `{{artifacts.<name>.commit}}`, `{{artifacts.<name>.branch}}`, `{{artifacts.<name>.path}}` (latest round of that artifact in this task). An unknown placeholder fails at config load, not at run time. Resolved values are always strings; the server takes `resource_id` as a string (confirmed live, §10).

Comparison is plain equality on the field's value, after converting both sides to strings **[builder call]**. Field names may be dotted paths into nested objects. A field that is missing from the result counts as not equal.

Check, `evidence`, `on_fail` and `next` come together: a step with `check` must have `evidence` and `on_fail`, and the check may only use evidence keys the step requires. Checks are allowed on agent work steps only, not on review steps or gates **[builder call]**.

### 5.3 Running a check

When a verifier calls `complete_step` on a step with a check:

1. In a first transaction, the core validates as today (claim, owner, token, outcome, required evidence keys), resolves the placeholders, and **commits nothing**.
2. **Outside any transaction**, it calls the tool on the external server through the `mcp` SDK's streamable HTTP client, with a timeout (30 s **[builder call]**).
3. It reads the result: `structuredContent` if present, otherwise the first text content parsed as JSON. A tool error, a timeout, an unparseable result or a missing credential → **refused** ("CI check could not run: …"), nothing changes.
4. In a second transaction, it reloads the step run and re-checks the claim (another operation may have happened in between), then applies the result:
   - `match` fails → refused, naming each mismatch ("head_sha is 3f2a…, expected 9c1e… (the latest change)"; "status is in_progress, expected completed"). The agent waits and calls again, or names another run.
   - `match` holds, `expect` fails → the run closes with outcome `failed`; `on_fail` opens as a new round; the next round's `feedback` says which fields differed and names the run.
   - both hold → the run closes `completed`; `next` opens.

Every attempt is in the history: the verifier's `complete_step` event (allowed or refused, with the reason), plus a **`check` event by `core`** with the server, tool, resolved arguments, the observed values of the `match`/`expect` fields (not the whole result) and `pass`/`fail` **[builder call]**.

## 6. Behaviour

### 6.1 Roles and identities

New roles: `builder`, `verifier`. `dev-issuer.yaml` gives both agent identities every agent role, so either can build and the other can review **[builder call]**:

```yaml
agent-a: {scopes: [taktloop:taktloop:planner, taktloop:taktloop:builder,
                   taktloop:taktloop:reviewer, taktloop:taktloop:verifier]}
agent-b: (same)
```

`tobias` is unchanged (requester, approver, reviewer, factory_owner). The permissions table needs no new rows: step operations already use the step's role.

### 6.2 Separation of duties (strict)

M2's rules already cover the new steps: rule 1 and 2 make `code_review` ≠ `build` because `code_review.review_of` is `build`. One change: rule 3's "nearest work step before the gate" must **skip steps without required artifacts** (like `verify`), so `integration_approval` approves `build`'s work, and an approver who built the change can't approve its integration **[builder call]**. Verifier ≠ builder is **not** required: the core checks CI itself **[builder call]**.

### 6.3 `complete_step` with evidence

`complete_step` gains `evidence: dict[str, str] | None`. A step with `evidence: [keys]` requires exactly those keys, non-empty; other steps refuse evidence **[builder call]**. Evidence is stored in the step run's existing `evidence` JSON (next to review findings), so no schema change is expected. The CLI needs no new command: verify is done by a session.

### 6.4 Feedback

`feedback.kind` gains `check_failed`: the text names the run and each `expect` mismatch (for example "CI run 12345 on 9c1e…: conclusion is failure, expected success"), `by` is `core`. Gate rejections and review issues work as in M2.

### 6.5 Waiting-for and history

- Verify step open: "waiting for a verifier to pick up *verify*"; claimed: as today.
- `task show` renders `check` events in one line ("CI check on run 12345: pass") and the `failed` outcome of a verify round.
- `build_escalation`: "waiting for approver decision at *build_escalation* (build failed review 2 times)", which M2's text already produces.

### 6.6 Integrate (known limit)

The approver decides at `integration_approval` (`taktloop approve <task>`); a builder session then claims `integrate`, merges the **change commit** (not the branch tip) into `main`, pushes, and completes with the merge commit. In M3a the core does **not** verify that `main` contains that commit, or that nothing else was merged. That's the job of branch protection with required PR approval, which @Tobias wants later. The merge commit is recorded so a later check can be added.

## 7. GitHub Actions

A workflow `.github/workflows/ci.yml` runs on every `push` (all branches) and on `pull_request`: checkout, install uv, `uv sync --locked`, `uv run pytest`. One job on `ubuntu-latest`, Python from `pyproject.toml` **[builder call]**. The suite must pass on the runner without network access to anything but GitHub and PyPI; tests that need a live GitHub token are skipped there (§9).

This lands first, alone, and is pushed to `main` right away so Actions is configured before the rest is built (@Tobias).

## 8. Code shape and bridge skill

**Code:**

- `workflow.py` (145 lines) grows the `evidence`, `check`, `on_fail` fields. The check's own parsing and placeholder validation go in a new module with the check runner, so `workflow.py` stays small **[builder call]**.
- New module for checks (for example `checks.py`): declaration, placeholder resolution, the MCP client call, result reading, `match`/`expect` comparison. Pure parts are separated from the network call so they are tested without a server.
- `ops_steps.py`: the two-transaction completion path for steps with a check.
- `config.py`: `mcp_servers`.
- The MCP client call reuses the pattern of `cli_commands._call_tool` where that fits.
- No module over ~300 lines; total ≤ ~2,380 non-test lines (§2).

**Bridge skill** (`templates/mvp/skills/taktloop/SKILL.md`):

- Code steps of the work: the artifact `path` is `"."` and `commit` is the commit that matters (base commit, pushed head, merge commit). Push the branch before completing build.
- Review steps whose `review_of` is `build`: review `workspace.commit..change.commit` of the change's branch.
- Verify: after local verification, find the run for the change commit (`gh run list --commit <sha>`), wait until it finishes, then `complete_step(..., evidence={"ci_run": "<id>"})`. A refusal for `status` means "wait and retry"; for `head_sha` it means the wrong run. Never claim a result; the core checks it.
- Integrate: merge the change commit, push `main`, complete with the merge commit. Never force-push.

## 9. M3a acceptance (the demo)

With the issuer and the core running (`TAKTLOOP_GITHUB_TOKEN` set for the core), `separation: strict`, on a small real change to this repository chosen by @Tobias:

1. A task goes through design, approval, plan and plan review as in M2.
2. A session as `agent-a` claims `workspace`, creates and pushes the branch, completes.
3. A session as `agent-a` builds and pushes; completes with the change commit.
4. `agent-a` is refused `code_review` (strict); `agent-b` returns *issues*; `agent-a` revises in build round 2; `agent-b` passes.
5. A session as `agent-b` verifies. While CI is still running, completion is refused ("status is in_progress"). Naming a run of an older commit is refused ("head_sha …"). After CI is green, the gate passes; a `check` event by `core` is in the history.
6. A deliberate red run (a failing test pushed in a build round) sends the task back to build with `check_failed` feedback naming the run, then through review and verify again.
7. `taktloop approve <task>` (as `tobias`) at `integration_approval`; a session as `agent-a` integrates, pushes `main`; the task is done.
8. `taktloop task show` shows the whole lifecycle with identities, verdicts, check results and the merge commit.

Automated tests (`uv run pytest`): workflow loading of `evidence`/`check`/`on_fail` (and the refusals: unknown server, unknown placeholder, check without `on_fail`, evidence key not required); placeholder resolution; result reading (`structuredContent`, JSON text, garbage); `match` refusal, `expect` failure to `on_fail` with feedback, pass; claim lost during the network call; network error and missing credential refusals; separation rule 3 skipping artifact-less steps; an end-to-end run with a **fake external MCP server** in the test process standing in for GitHub. One **live** test against GitHub's real MCP server, run only when `TAKTLOOP_GITHUB_TOKEN` is set, checks the tool name, argument types and field names on a known run of this repository.

## 10. Constraints

- Everything from M1 §8 and M2 §10 still holds (Python ≥ 3.12, uv only, SQLite, small modules, English docs, product name "taktloop", `.taktloop/` gitignored).
- Direct runtime dependencies unchanged: `mcp`, `pyyaml`, `pyjwt[crypto]`.
- **GitHub's MCP server, confirmed live on Nov 2, 2026** with a fine-grained token (Actions: read, Metadata: read on `medlor/taktloop`) against `https://api.githubcopilot.com/mcp/x/actions/readonly`, run 41508346956 of `ci.yml`:
  - tools offered: `actions_get`, `actions_list`, `get_job_logs`;
  - `actions_get` takes `method` (`get_workflow_run`), `owner`, `repo`, `resource_id` (a **string**);
  - the result comes as both `structuredContent` and identical JSON text: `{"method": "get_workflow_run", "workflow_run": {"id", "head_sha", "head_branch", "path", "status", "conclusion", "event", "html_url", …}}`;
  - an unknown run id is a tool error (`isError: true`, text "failed to get workflow run: … 404 Not Found"), which §5.3 treats as "check could not run".
- The GitHub token is the core's own, read-only, scoped to this repository, and never written to `factory.yaml`, the database, logs or events.

## 11. Left to the builder

Exact module names and split; pydantic models; the check event's exact detail fields (within §5.3); the CLI rendering of checks and failed rounds; the bridge skill's wording (within §8); the CI workflow's details (within §7); the README update for M3a, including how to create the fine-grained token.

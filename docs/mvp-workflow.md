# MVP default workflow, aligned with superpowers skills

Status: draft · Nov 1, 2026 · @Tobias

> Text marked **[proposal]** is a suggestion that hasn't been agreed yet. **[open]** marks a question still to answer.

The MVP factory is **taktloop building itself** (see `prd.md`). @Tobias already works with the [superpowers](https://github.com/obra/superpowers) skills (vendored locally from upstream commit `2ce8aa1`). This document aligns the MVP's default workflow with those skills, so that each step's role runs the skills it already uses.

## 1. The superpowers basic workflow

From the superpowers README and the skill files:

1. **brainstorming:** draws out what you want and why, plays it back, and lays out the approaches. Writes a design doc to `docs/superpowers/specs/YYYY-MM-DD-<topic>-design.md`. **You approve the design** before anything is built.
2. **using-git-worktrees:** an isolated workspace on a new branch, with a verified clean test baseline.
3. **writing-plans:** bite-sized tasks with exact file paths, code and verification steps, saved to `docs/superpowers/plans/YYYY-MM-DD-<feature>.md`. **You review the plan** and pick how it's executed.
4. **subagent-driven-development** (fresh subagent per task, review after each) **or executing-plans** (inline, one final review).
5. **test-driven-development:** red → green → refactor during implementation.
6. **requesting-code-review:** reviews against the plan; critical issues block progress. **receiving-code-review** handles the feedback with rigor.
7. **finishing-a-development-branch:** verifies tests, then **you choose**: merge locally, push and open a PR, keep as-is, or discard.

Supporting skills: **verification-before-completion** (evidence before any "done" claim), **systematic-debugging** (root cause before fixes), **dispatching-parallel-agents** (independent tasks in parallel).

## 2. MVP workflow with roles and skills

| # | Step | Role | Superpowers skill(s) | Output (evidence in the core) | Gate to leave the step |
| --- | --- | --- | --- | --- | --- |
| 1 | **Intake** | requester | — | task created with a short request | — |
| 2 | **Triage** | triage | brainstorming's first check ("What do you actually know?"): quick task vs. conversation, small change vs. project | route: *quick task*, *small change* or *project*; or *park* | — |
| 3 | **Design** | planner, with the requester | brainstorming (incl. builder check) | design doc in `docs/superpowers/specs/` | **human: design approved** (approver) |
| 4 | **Plan** | planner | writing-plans (incl. its self-review) | plan in `docs/superpowers/plans/` | — |
| 4a | **Plan review** | reviewer (a second agent, ≠ planner) | taktloop plan-review prompt (see §2.1), modelled on brainstorming's builder check | verdict: *pass* or *issues*; issues → back to 4 | **agent gate: plan review passed**; no human approval |
| 5 | **Workspace** | builder | using-git-worktrees | branch + clean test baseline | — |
| 6 | **Build** | builder | subagent-driven-development or executing-plans; test-driven-development; systematic-debugging; dispatching-parallel-agents | commits on the branch | — |
| 7 | **Review** | reviewer | requesting-code-review (reviewer side); receiving-code-review (builder side, on the way back) | review verdict; critical issues → back to 6 | reviewer ≠ builder (separation of duties) |
| 8 | **Verify** | verifier | verification-before-completion | test/CI result | **hard gate: CI green**, checked by the core through the CI system's MCP server (ADR 0002) |
| 9 | **Integrate** | approver decides; builder executes | finishing-a-development-branch | merge commit or PR, kept branch, or discarded | **human: integration decision** (merge / PR / keep / discard) |
| 10 | **Done** | — | — | task closed | — |

### 2.1 Plan review by a second agent

Decided: **the plan needs no human approval** in the MVP. A second agent reviews it instead.

**What superpowers provides.** Superpowers has no dedicated plan-review agent:

- **writing-plans** ends with a **self-review**: a checklist run by the same agent that wrote the plan (does every spec requirement have a task, are there placeholders, are names consistent, are the risky cases tested). Useful, but not independent.
- **brainstorming** has a **builder check** (`builder-check-prompt.md`): a fresh subagent that reads the *design doc* as a builder who can't ask questions, and reports what it would still have to guess. That's the closest pattern.
- **requesting-code-review** reviews *code* against a plan, not the plan itself.

**[proposal] taktloop's plan review** reuses these instead of inventing something new. A fresh agent in the reviewer role (never the planner's identity) gets the design doc and the plan, and checks:

1. **Builder check, applied to the plan:** "You must implement this plan without talking to its author. Where would you have to guess, and where would a wrong guess matter?"
2. **writing-plans' self-review checklist, run independently:** spec coverage, no placeholders, consistent names, tests for the risky cases.

The outcome is *pass* (go to workspace/build) or *issues* (back to the planner with the findings). **[proposal]** After 2 failed rounds the task is escalated to a human, so the loop can't run forever.

The prompt is small and lives with taktloop (as part of the bridge skill or as its own skill), because superpowers doesn't ship one.

**Execution mode without a human.** writing-plans normally asks the human to review the plan and choose an execution mode. In the factory, the bridge skill tells the planner not to wait. The execution mode (subagent-driven or inline) comes from the factory's configuration.

Routes from triage, **as built in M4**. Before triage, the orchestrator opens the task's ticket (the `open_ticket` code step). Triage is a pushed headless step: the orchestrator starts a read-only run as `triage-bot`, which uses the `taktloop-triage` skill and completes the step with one route and a one-line reason:

- **quick** (for example "make the icon blue"): straight to workspace, 5 → 9. With no plan, build implements the request and code review checks against it. Code review, CI green and the integration approval still hold.
- **small:** straight to plan, 4 → 9. With no design doc, the plan is written from the request and plan review checks it against the request.
- **project:** the full path 3 → 9.
- **park:** the request isn't ready or triage can't tell. The task waits at the `triage_decision` gate, where an approver picks one of the three routes or `cancel` (`taktloop route <task> <route> --reason ...`). Parking is never a backlog: a gate nobody decides within `stall_after_minutes` is reported as stalled.

An approver can also set the route at intake (`taktloop task create ... --route quick`); the ticket is still opened first and triage is then skipped. The route and its reason are in the task's history.

## 3. Skills outside the delivery flow

| Skill | Role in taktloop |
| --- | --- |
| **writing-skills** | **factory assistant:** writing or changing skills during a breakout (PRD use case D), approved by the factory owner |
| **diagnosing-superpowers** | **factory owner / auditor:** finding out why a run went wrong |
| **using-superpowers** | loaded by every agent role as the entry point for finding skills |

## 4. What changes when superpowers runs inside taktloop

Superpowers runs **one session with one operator**. taktloop turns that session into **durable, role-separated steps**:

| In superpowers today | In taktloop |
| --- | --- |
| Checkpoints ("approve the design", "pick an option") are questions in the chat; the session waits | checkpoints are **human gates in the core**. The task waits durably, and any identity with the approver role can pass the gate, later and from any client |
| Planner, builder and reviewer are the same session and operator (the reviewer is a fresh subagent of it) | each is a **separate role with its own identity**; the core enforces reviewer ≠ builder |
| "Verified" means the agent ran the checks and reported evidence (verification-before-completion) | the agent's evidence is kept, **plus** a hard gate where the core itself checks the CI result |
| Design doc and plan are files in the branch | they stay files in git; the core records them as **artifact references** on the task |
| The workflow lives in the skills' instructions | the workflow lives in the core; each role's executor is configured with **only the skills of its steps** (ADR 0001) |

The skills themselves don't need to change. Each step's executor runs the matching skills, and only the hand-offs between steps move into the core.

**How a harness takes part** ([ADR 0004](adr/0004-executors-pull-steps-via-bridge-skill.md)): it pulls an open step from the core, claims it, runs the skill the step names, and reports completion back to the core. A taktloop **bridge skill** teaches it this lifecycle and makes it stop at the step boundary. Without that, superpowers would chain straight from brainstorming into writing-plans.

## 5. Open

- [x] **Interactive design.** Decided: design is a **human-driven step inside a harness**. The requester's harness pulls the open design step from the core, runs brainstorming with the user, and reports completion with the design doc as evidence. See [ADR 0004](adr/0004-executors-pull-steps-via-bridge-skill.md).
- [x] Is plan approval (step 4) a separate human gate? No: for now a second agent reviews the plan (step 4a, §2.1).
- [x] For the MVP's "CI green" gate: decided, **real GitHub Actions runs on this repository, checked by the core through GitHub's remote MCP server** (ADR 0002). The core needs its own read-only credential for that (Actions and repository contents only), provided through the factory's configuration, not by the agents.

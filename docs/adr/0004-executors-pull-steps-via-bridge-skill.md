# ADR 0004: Executors pull open steps from the core, guided by a taktloop bridge skill

- Status: Proposed
- Date: 2026-11-01
- Deciders: @Tobias

> Text marked **[proposal]** is a suggestion that hasn't been agreed yet. **[open]** marks a question still to answer.

## Context

Workflow steps are assigned to roles, and roles are filled by executors, usually an agent harness such as Claude Code (see `rbac.md`, `comparison.md`). Some steps are human-driven inside a harness. The design step, for example, is a brainstorming conversation between the requester and the agent, using the superpowers skills (see `mvp-workflow.md`).

That raises the question of how an executor finds work, and how it reports back to the core.

## Decision

1. **Executors pull work.** A harness asks the core MCP server for open steps it may do (filtered by its roles from its token, ADR 0003), picks one, works on it, and reports the result back to the core as an event.
2. **Human-driven steps work the same way.** For the design step, the requester's harness session asks for open steps, sees the open design step, claims it, and runs the brainstorming skill with the user until the skill considers the design complete. Then it sends the completion event, with the design doc as evidence, to the core.
3. **Pull is the default; push is the exception.** Some steps need to be pushed to someone, for example when no executor is polling (a headless run that has to be started) or a person has to be notified. For those, the **taktloop orchestrator** monitors the task states and pushes the step to its executor.
4. **The taktloop orchestrator manages events and task transitions.** It is a core component, not necessarily an agent. It uses an AI agent **only for operations that need one**. Where plain rules suffice (opening the next step, starting a run, sending a notification), no AI is used.
5. **taktloop ships a bridge skill.** Besides the core MCP connection, taktloop provides a **skill** that tells the harness how to work with the core: how to find and claim open steps, how to run a step, and how to report completion or failure. Agent definitions per role are deferred (see below).

## Pull and push [proposal]

The core works out the open steps **deterministically** from each factory's workflow. No agent is needed to decide *which* step is next. Each step declares how it reaches its executor:

```yaml
step: build
role: builder
dispatch: pull            # default: an executor asks for it
---
step: verify
role: verifier
dispatch: push            # someone has to start it
push: {start: claude-code-headless, skill: verification-before-completion}
---
step: design_approval
role: approver
dispatch: push
push: {notify: requester-channel}
```

The **taktloop orchestrator** watches task states and takes care of everything that doesn't happen by itself. Each operation uses AI only if it needs judgment:

- **pushes** steps marked `push`: starts a headless run, or notifies a person. *Plain rule, no AI;*
- **watches for stalls:** steps nobody claims, leases that keep expiring, tasks waiting too long on a person, and escalates them to the factory owner. *Detecting a stall is a rule; deciding what to do about an unusual one may use AI;*
- **raises out-of-sync events** from external systems that need attention (for example a deleted ticket, PRD §3a).

Judgments inside the flow, such as triage ("quick task, small change or project?"), are **ordinary steps** done by their role, not by the orchestrator.

## The step lifecycle [proposal]

Core MCP tools for executors:

| Tool | Purpose |
| --- | --- |
| `list_open_steps()` | open steps the caller's roles may do, with task, step and a short summary |
| `claim_step(step_id)` | take a step. The core grants a **lease** (an exclusive claim with an expiry), so two executors never work on the same step |
| `get_step_context(step_id)` | everything needed to do the step: the task, earlier artifacts (for example the design doc for the plan step), the step's instructions and skill, its allowed tools (ADR 0002), and the expected output contract |
| `heartbeat(step_id)` | extend the lease during long steps (for example a long design conversation) |
| `complete_step(step_id, outcome, evidence)` | report the result, for example `design.completed` with an artifact reference to the design doc. The core validates it against the step's contract and gates |
| `release_step(step_id, reason)` | give a step back unfinished, for example when the user stops for today |

An expired lease returns the step to the open list, so abandoned work never blocks a task.

## The bridge skill [proposal]

The `taktloop` skill is **generic**. It knows the core's tools and the step lifecycle above, and nothing about superpowers or any other skill set:

1. Ask the core for open steps; offer them to the user, or pick one when running headless.
2. Claim the step and read its context.
3. Run the step **using the skill the step names**, for example "use the `brainstorming` skill". Which skill each step uses is part of the factory's workflow configuration in the core, not of the bridge skill. That keeps taktloop independent of superpowers.
4. **Do only this step.** When the step's output exists, report it to the core and stop. Don't continue into the next step on your own.
5. Report failure or hand the step back when it can't be finished.

Point 4 matters because superpowers chains its skills: after brainstorming, it moves straight on to writing-plans. Inside taktloop, the next step may belong to another role, another person or another harness, so the bridge skill has to set the step boundary explicitly and take precedence over the chaining.

## Skill or agents?

| Option | Pros | Cons |
| --- | --- | --- |
| **One bridge skill** | works in any harness that supports skills; one small file to maintain; fits KISS | relies on the agent following the skill |
| **Agent definitions per role** (for example a `taktloop-builder` subagent) | role setup (tools, skills, prompt) packaged per role | formats differ between harnesses, so one definition per harness and role; more to maintain |

**Decided:** start with **one bridge skill**. Per-role agent definitions are added later only if they're needed for better steering or clarity.

## Implementation note (M3b)

The taktloop orchestrator is a **separate process** (`taktloop orchestrator`), not part of the core. It is an ordinary MCP client of the core with its own identity and role, and uses the core's tools only (`sweep`, `report_stall`, `claim_step`, `complete_step`, ...). It runs code steps, starts headless executors for steps marked `dispatch: push`, and reports stalls; the core keeps deciding every gate. A headless executor is told its step in the prompt ("work on taktloop step <id>") and the bridge skill claims exactly that step.

## Implementation note (M4): hooks

Reactions to what happens in the core (mirroring a task's step into its ticket, closing the ticket when the task ends, a notification on a stall) are **hooks**, fired by the **orchestrator** from the core's event history, not workflow steps. The orchestrator reads `hooks.yaml` next to `factory.yaml` (re-read on change), pages through the core's events with `list_events`, and for each matching event runs one declared MCP call (or one command, the escape hatch). Each hook keeps its own cursor and is at least once, with three attempts on failure; a hook whose server is down waits and catches up in order. A failing hook never blocks a task, and the core still never writes to the outside world. The core gained two events for this, `step_opened` and `task_closed`.

The triage step is a pushed headless step like `code_review`: the orchestrator starts a read-only run as `triage-bot`, which picks a route with the `taktloop-triage` skill.

## Consequences

**Positive**

- No push infrastructure is needed to reach executors. They connect to the core when they're ready, which suits laptops, sessions that end, and humans.
- Human-driven and headless steps follow the same lifecycle.
- Leases make abandoned steps recover by themselves.
- Existing skill sets (superpowers) are used unchanged. The bridge skill only adds step boundaries and reporting.

**Negative / risks**

- Headless executors have to poll or be started when work exists. Something has to wake them: the taktloop orchestrator starts them for steps marked `push` (see Open).
- Step boundaries depend on the agent following the bridge skill over the chaining instructions in other skills. This needs testing with superpowers specifically.
- The core must validate every `complete_step` against the step's contract, because a harness can report anything.

## Open

- [x] How headless executors are started when work appears: by the taktloop orchestrator, for steps marked `push`.
- [x] Do simple pushes need an agent? No: the taktloop orchestrator uses AI only where an operation needs judgment.
- [x] Bridge skill only, or additional agent definitions per role? Start with the skill; agents later if needed for steering or clarity.
- [x] With executors pulling work, how much does the orchestrator agent from ADR 0001 still need to do? It becomes the **taktloop orchestrator**: a core component that manages events and task transitions, pushes steps that need pushing and escalates, using AI only where needed.

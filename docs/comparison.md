# taktloop vs. "just use an agent harness"

Status: investigation · Nov 1, 2026 · @Tobias

## Question

Agent harnesses such as Claude Code, Copilot CLI or Codex already offer subagents, per-agent skills and MCP servers, plans, hooks, headless runs and scheduling. Why not run a software factory with a harness alone? What does taktloop add, what does it cost, and when is it worth it?

Claude Code serves as the reference harness below. Other harnesses differ in details, and feature sets change fast, so verify specifics before relying on them.

## What a harness already gives you

| Capability | Agent harness (e.g. Claude Code) | Relevance for a factory |
| --- | --- | --- |
| Agent loop with tools | yes: the core of the product | the work itself (spec, code, review) |
| Subagents with their own prompt, tools/MCP and skills | yes | can play individual roles (planner, builder, reviewer) |
| Skills (packaged instructions + scripts) | yes | matches ADR 0002's "MCP server + skill" plugin |
| Long, multi-step work from a written plan | yes: plan files, plan mode, task lists, context compaction | one task's implementation phase |
| Deterministic checks on tool calls | yes: hooks and permission rules | can block forbidden actions locally |
| Headless / programmatic runs | yes: CLI non-interactive mode, Agent SDK | can be started per event |
| Triggers from GitHub / CI | yes: GitHub Action integration, issue and PR mentions | event intake, but GitHub-centric |
| Scheduling | yes: scheduled / recurring runs | timers, nightly jobs |
| Isolation | yes: git worktrees, sandboxing, cloud sessions | disposable environments |

**The overlap is large.** For one developer working on one task at a time on GitHub, a harness with a few skills, an MCP server or two, hooks and a GitHub Action already covers most of a "factory".

## Where a harness alone falls short

A harness is built around a **session**: one agent (with subagents) working toward one goal, run by one operator. A factory is built around **tasks that outlive any session and involve several people and agents in different roles**. The gaps follow from that difference.

1. **Task state across days and sessions.** A capability can sit for two days waiting for a spec approval, then go through several implementation and CI rounds. A harness session doesn't hold that state. You'd have to keep it somewhere else (files, issue labels), which means building taktloop's core informally.
2. **Event routing.** Something has to receive webhooks, timers, human replies and CI results, work out which task each belongs to, and wake the right role with the right context.
3. **Roles enforced outside the agent.** A harness's permissions and hooks are set by whoever runs it, can be switched off locally, and guard *tool calls*. taktloop's RBAC (see `rbac.md`) checks **every operation on the task** against the caller's role, from an identity the organization's IAM issued (ADR 0003). That holds regardless of which harness, script or person fills the role.
4. **Separation of duties.** "The builder may not approve its own work" is a rule across identities and across time. A single harness session can't enforce it, because the same operator runs builder and approver subagents. The core enforces it from its own records.
5. **Gates the agent can't bypass.** Hard gates are verified by plain code in the core through the tool's MCP server (ADR 0002), not by the agent's report or local hooks.
6. **Many tasks, one view.** "Where is every capability right now, what is it waiting for, and why?" A harness shows its own session only.
7. **People in the workflow.** Approvals by someone other than the person running the agent, hand-offs, reminders and escalations when a human step stalls.
8. **Harness independence.** Each role is bound to an executor per factory (role → executor binding): Claude Code for the builder, Copilot CLI for the reviewer, a human for the approver, a script for the releaser. Switching harness changes a binding, not the workflow or its rules.
9. **Audit per task.** One durable record per task of every operation: who (identity), in which role, what, and why, plus model and prompt for agent decisions.

## Where the harness wins (taktloop's costs)

- **Zero extra system.** Nothing to deploy, operate or secure. taktloop adds a service, a database and an endpoint.
- **Already mature.** Context management, tool use, subagents and sandboxing are polished and improve at the vendor's pace. taktloop must not compete there.
- **Simpler for one person.** A solo developer on one repo gets little from roles, durable task state or a portfolio view.
- **Faster iteration.** Changing a skill or prompt is immediate; taktloop adds workflows, role bindings and gate declarations to maintain.
- **Risk of duplication.** If taktloop builds its own agent loop, it rebuilds what harnesses already do well.

## When which fits

| Situation | Harness alone | taktloop + harness |
| --- | --- | --- |
| One developer, one repo, tasks finished within a session or a day | ✅ enough | overkill |
| Tasks that wait on people for days (spec approval, release sign-off) | ⚠️ state lives in ad-hoc files/labels | ✅ |
| Several tasks in parallel, several people | ⚠️ no shared view | ✅ |
| Separation of duties (builder ≠ reviewer ≠ approver) required | ❌ same operator runs every subagent | ✅ |
| Hard gates that must hold no matter who runs the agent | ⚠️ local hooks only | ✅ |
| Agents need their own identities instead of developers' accounts | ⚠️ depends on setup | ✅ every caller authenticated via IAM |
| Different harnesses or humans per role, or switching harness later | ❌ tied to one harness | ✅ |
| Compliance / audit per work item | ⚠️ scattered transcripts | ✅ |

## Conclusion

taktloop shouldn't compete with harnesses; it should **sit around them**. Its differentiators are what a session can't hold: **durable task state, event routing, roles and separation of duties enforced outside the agent, gates verified by plain code, a view across all tasks, people in the loop, and harness independence.**

**Resulting split:**

- **taktloop builds:** the core MCP server as the single source of truth (task state, workflow, gates, RBAC from external OAuth tokens, audit log) and event intake/routing. Clients, harnesses and the later dashboard all go through it.
- **taktloop borrows:** the agent runtime. The AI parts of the taktloop orchestrator and every work role (planner, builder, reviewer, verifier, …) run in whatever executor the factory binds to them, typically an existing harness such as Claude Code. Identities come from the organization's IAM.

Because the core is an MCP server that validates standard OAuth tokens, any MCP-capable harness can fill any role. taktloop stays harness-independent while writing almost no agent code.

**Counter-check.** If a factory's tasks are short, single-person and GitHub-only, taktloop adds little. Its value grows with task duration, parallel tasks, the number of people and roles involved, and how strictly gates and duties must be enforced. That's why the target is teams and enterprises (PRD §4).

# taktloop — Product Requirements Document

Status: draft v4 · Nov 1, 2026 · @Tobias

> Working draft, written together in a brainstorming session. Text marked **[proposal]** is a suggestion that hasn't been agreed yet. **[open]** marks a question still to answer.

## 1. Purpose

taktloop is a **very lean orchestrator for software factories**. It takes one unit of software work, such as a new capability for a product, from idea to its finished end state. It keeps the steps in the right order, holds the threads between them, and shows people where each piece of work currently stands.

taktloop doesn't do the work itself, and it doesn't rebuild the tools that do. Task tracking, code hosting, CI/CD, cloud deployment and coding agents stay with the best tools available for each job. taktloop connects them through plugins, and the **factory owner** picks whichever tools fit them best.

The end result is always the same kind of thing: **an existing software product gains a new capability.** That keeps the scope narrow on purpose.

## 2. Problems today (without taktloop)

These are the problems teams face today; taktloop is meant to solve them.

### 2.1 Nobody knows yet what software development will look like

AI is changing how software is built faster than tools settle. Coding agents, harnesses, models and the platforms around them are changing every few months. Committing a whole organization to one integrated ecosystem now risks lock-in to a system that may turn out to be the wrong one. Organizations need to adopt AI-driven development **now** without betting everything on today's winner. Components have to stay swappable.

### 2.2 Enterprises can't simply adopt one integrated ecosystem

An integrated ecosystem such as GitHub (Issues, Projects, coding agents, Actions) already covers a large part of the idea-to-deploy path. In enterprises, though, some systems are often mandated for certain jobs. For example, Atlassian Jira may be required for work tracking even when code lives on GitHub.

### 2.3 Every unit builds its own delivery setup

Large organizations often lack a central, shared solution for delivering software. Each use case, sub-product or department builds and runs its own DevOps process, IDE setup, implementation workflow and pipelines. This duplicated effort is slow and expensive, and the knowledge doesn't transfer between units.

### 2.4 Long-running work has no single source of truth

A capability takes days or weeks from idea to production. It waits on people (spec approval, release sign-off), goes through several implementation and CI rounds, and touches many tools. Agent harnesses work in sessions that end. Trackers hold state that people update by hand. CI knows only its own runs. Nobody can reliably answer "where is every capability right now, what is it waiting for, and why?"

### 2.5 Agents act with too much and unclear authority

Coding agents typically run with a developer's personal credentials, so they can do far more than their step needs. The same operator runs the agent that builds and the agent that reviews. Approvals happen inside one session or by convention. That leaves gaps that teams and enterprises can't accept:

- **No separation of duties:** nothing stops the builder from approving its own work.
- **Gates are soft:** they depend on local hook or harness settings that can be switched off, or on the agent's own report.
- **No clear audit:** "who allowed this change, in which role, based on what evidence?" is scattered across session transcripts.
- **Large blast radius:** a prompt injection in a ticket or PR comment can make an over-privileged agent do anything its credentials allow.

### 2.6 What's missing

There is no **lean** solution that:

- gives teams a **practical default workflow structure**, built from the organization's typical building blocks, which explains itself and saves time and money;
- lets each team **break out of that structure easily** when it needs to, to cope with the fast-changing AI-era landscape;
- holds **one durable source of truth** for every task's state across sessions, people and tools;
- applies **one role model to humans and agents alike**, with separation of duties and hard gates enforced outside any harness, using the organization's existing identity provider; and
- does all this **by connecting the best existing tools and harnesses**, not by replacing them with yet another heavy platform.

## 3. Concepts

- **Factory:** one self-contained taktloop instance for one use case or product. It has its own workflows, its own plugin set and its own configuration. Factories don't change each other; reusable pieces are shared only through templates (see Default workflow).
- **Factory owner:** the person or team responsible for a factory. They choose its tools, adapt its workflows and add plugins.
- **Factory lifecycle** (post-MVP): once taktloop supports tenants with several factories each, every factory goes through a lifecycle:

  **intake** (the idea for a new factory / process) → **implementation** → **test run** → **live** → **temporarily disabled** → **decommissioned**

  A factory accepts real tasks only while live. "Staging" a factory means moving it towards live, and it requires that all MCP servers it depends on are available (ADR 0002). **[proposal]** The lifecycle of factories could itself be run by a taktloop factory, which would be the same machinery applied to itself.
- **Default workflow:** the practical starting structure a new factory begins with, built from typical building blocks. **taktloop ships ready-made workflow templates, including their step pieces**: the skills (for example the bridge skill and the plan-review prompt), the code-step and gate-check declarations (for example "trigger GitHub Actions", "CI green"), and the role bindings they expect. MCP servers themselves are not shipped; a template only refers to them (ADR 0002). The first is the MVP workflow aligned with the superpowers skills ([mvp-workflow.md](mvp-workflow.md)). **[proposal]** A new factory gets a *copy* of a template, which its factory owner then adapts; later template updates don't change existing factories automatically (KISS).
  **Post-MVP: shared catalog through git.** An organization keeps its own **template repository** (a git repo of templates and step pieces, building on taktloop's shipped templates). A factory owner proposes a breakout for it as an ordinary pull request; the repository's owner reviews and merges it; new factories pick it up when they copy a template. No registry service in taktloop.
- **Task:** one unit of software work moving through a factory's workflow toward a defined end state (for example "merged" or "deployed").
- **Step component:** a reusable kind of step (for example implementation, continuous deployment) with a strict input/output contract.
- **Role:** what someone may do in a workflow step: a responsibility plus permissions in the core (for example planner, builder, reviewer, approver). See [rbac.md](rbac.md).
- **Executor:** who or where a role runs: a harness such as Claude Code, Copilot CLI or Codex, a script, or a person.
- **Binding:** per factory, which executor fills which role. Claude Code for every role is a valid binding, never a requirement.
- **Plugin:** connects a step component to a concrete tool (for example "continuous deployment via Azure DevOps"). At its core a plugin is **an MCP server plus a skill** that tells the agent how to use it in this factory's workflow, including which credential and scopes the server requires ([ADR 0002](adr/0002-external-tools-via-their-own-mcp-servers.md)). Common plugins ship with templates; a factory's own plugins stay local until promoted to the template repository.

## 3a. How taktloop works

- **Core MCP server as the single source of truth.** It holds task state, workflow and gates, enforces RBAC and keeps the audit log, and it does event intake and routing. Clients, harnesses and the later dashboard all go through it.
- **Borrowed agent runtime.** taktloop builds no agent loop of its own. The AI parts of the orchestrator and every work role run in whatever executor the factory binds to them, typically an existing harness. See [comparison.md](comparison.md).
- **What "single source of truth" covers.** The core is authoritative for **task state, workflow, gates and the audit trail**, and nothing else. External tools keep their own state (a Jira ticket's status, a PR's merge state, a CI run), which taktloop neither owns nor controls; someone can still merge a PR directly in GitHub. Such changes reach taktloop only as events. They advance a task only if the workflow allows that move and its gates pass, otherwise they are recorded and ignored or flagged.
- **Out-of-sync handling depends on the external system.**
  - **Ticket system:** a change that contradicts taktloop's state (for example a ticket of an active task is deleted or closed by someone) is **raised to the factory owner**. To detect this, the core reads ticket status itself through the ticket system's MCP server (in the MVP the local stand-in), with its own credential, the same way the gate checker reads CI results.
  - **Code hosting and CI/CD:** out-of-band changes (for example a direct merge) need no special handling. Existing mechanisms such as CI pipelines, validations and later refactoring already deal with them transparently. taktloop records the event, and that's all. Enforcing rules like "no merge without review" *inside* GitHub stays with the tool's own settings (for example branch protection), configured by the factory owner.
  - **[proposal]** Each plugin declares its default (`escalate` or `record`), and the factory owner can override it per factory.
- **RBAC from external tokens.** Every operation on the core is authorized from an OAuth token, with roles read from scopes such as `taktloop:<factory>:<role>`. In production the organization's identity provider issues the tokens and taktloop mints none; for development and the MVP a tiny, separate dev issuer stands in. See [ADR 0003](adr/0003-rbac-with-external-oauth-tokens.md).
- **Pull by default, push when needed.** The core determines open steps deterministically from the workflow. Executors (harnesses, people) pull steps, do them and report back, guided by a taktloop bridge skill. The **taktloop orchestrator** manages events and task transitions, pushes steps that need it (starting headless runs, notifying people) and escalates stalls. It uses an AI agent only for operations that need judgment. See [ADR 0001](adr/0001-agent-driven-orchestration-via-mcp.md) and [ADR 0004](adr/0004-executors-pull-steps-via-bridge-skill.md).
- **Scoped tools.** Each step declares its skill and its allowed MCP tools. The bridge skill passes them to the harness; the hard limit is the scope of the executor's own credentials (ADR 0002).
- **Local, minimal services as MVP stand-ins.** For the MVP, taktloop ships its own local MCP servers offering only the most basic information and operations a workflow needs, backed by lightweight open-source libraries where available (for example for tickets). They stay as long-term defaults only if that's nearly free. See [ADR 0002](adr/0002-external-tools-via-their-own-mcp-servers.md).
- **External tools directly.** When a factory uses an external tool such as Jira, its agent uses that tool's own MCP server (not provided by taktloop) plus a skill. There is no taktloop service layer in between.
- **Interfaces:** MCP, A2A and a plain API (MVP: MCP, API and a CLI).

A picture of the MVP factory with all components, MCP servers, clients and where skills run is in [system-overview.html](system-overview.html).

## 4. Users and use cases

**Target: teams and enterprises.** taktloop is for organizations where several people and agents work on capabilities that take days or weeks, where tools are partly mandated, and where separation of duties, gates and audit matter. A solo developer on one repo is **not** the target; an agent harness alone serves them well (see [comparison.md](comparison.md)).

**Users**

- **Factory owner:** sets up a factory for their use case, picks the tools, binds roles to executors, adapts the workflow.
- **Developers:** run their capabilities through the factory's workflow, often as requesters or in work roles.
- **Approvers:** hold the human gates (spec approval, release sign-off).
- **Viewers and auditors:** follow progress and check what happened and why.

The full role model is in [rbac.md](rbac.md).

**Use case A: full lifecycle.** A developer owns a new capability: planning, brainstorming, spec, implementation, then triggering CI/CD and deployment. This is one task with many sub-steps. `idea/example_flow.md` shows one possible shape (background, not a requirement).

**Use case B: lightweight flow.** A developer with no CI/CD in the loop assembles a shorter flow whose goal is just "feature merged".

**Use case C: different factories, different flows.** Product A and product B use different flows because their teams chose different stacks: AWS vs. Azure, different CI/CD systems, or a product that itself runs as n8n workflows vs. high-code Python on ECS.

**Use case D: breaking out of the default.** A factory owner needs a step the default workflow doesn't offer, for example a different deployment target. Through a chat interface, AI helps them find a matching plugin (or write a new skill for an existing MCP server) and change their factory's workflow. The change applies only to their factory.

## 5. Scope

**In scope (eventually)**

- Orchestrating the steps of a software-development task in the right order.
- Default workflows built from typical building blocks.
- AI-assisted breakout: a chat interface to adapt or extend a factory's workflow, with the organization's template repository (git) as the shared catalog.
- Ephemeral execution environments: (cloud) containers or VMs created for a step such as implementation and thrown away afterwards.
- A visualization of each task's current state, derived from the underlying state graph.
- Multi-tenancy: tenants with several factories each, run for different units of an organization.
- Factory groups (for example per department) that share role grants across their factories, to simplify management.
- A factory lifecycle: intake → implementation → test run → live → temporarily disabled → decommissioned.

**MVP**

**Goal:** prove two things at once:

1. **taktloop is lightweight:** small, easy to maintain, KISS. Little own code, standard building blocks (MCP, OAuth), no own agent loop, no own credential handling.
2. **It solves the problems in §2** for a real team workflow.

**Shape:**

- One factory, no multi-tenancy, no factory lifecycle, no UI (CLI, API and MCP first). The single factory is configured and started; the availability check for its MCP servers runs at startup.
- The core MCP server (state, workflow, gates, RBAC, audit, event intake).
- A local minimal ticket stand-in instead of an external tracker (ADR 0002). Code hosting and CI are real: this repository on GitHub, and the "CI green" hard gate checks real GitHub Actions runs through GitHub's remote MCP server.
- Roles filled by an existing harness and by humans.

**[proposal] Success criteria**, each tied to a problem:

| Problem | The MVP shows that … |
| --- | --- |
| 2.4 No single source of truth | a capability runs from idea to merged/deployed across several sessions and a multi-day human wait, and its state, history and "waiting for what" are answerable at any time from the core |
| 2.5 Unclear authority | every operation is authorized from an external OAuth token; a builder identity can't approve its own work; a hard gate refuses to advance without verified evidence; every operation is in the audit log |
| 2.1 Lock-in | one role is switched to a different executor (for example builder from Claude Code to another harness or a human) by changing only the binding |
| 2.6 Lean | the core stays within the lightweight budget below |
| 2.6 Default + breakout | a factory starts from a default workflow and adds or changes one step without touching the core |

**Lightweight budget.** The MVP counts as lightweight only if all of these hold:

| Area | Criterion |
| --- | --- |
| Architecture | a simple architecture of **fewer than 10 components**, each clear, small and with one responsibility |
| Code size | the core is **at most ~3,000 lines** of code |
| Dependencies | **at most ~10 runtime dependencies** |
| Runtime | **one process with a SQLite file**; no broker, no extra services |
| Code quality | built to software-architecture best practice: small entities, separation of concerns, clear dependency directions, so a newcomer can understand it quickly |
| Setup | a **new factory is set up in under 30 minutes** |
| User documentation | **short and easy to understand**: a newcomer gets from zero to a running factory with it alone |

**Out of scope**

- A general-purpose workflow engine (like n8n). taktloop is only for software development.
- Rebuilding what existing tools do: no own task tracker, no own CI/CD, no own code hosting, no own coding agent.
- A heavy internal developer portal (service catalogs, scorecards, dashboards full of information).

## 6. Principles

1. **Software development only.** Every flow ends in a change to a software product.
2. **KISS.** Prefer the simplest solution that works, standard protocols over own ones, and existing systems (harnesses, identity providers, tools) over own code.
3. **Very lean.** taktloop orchestrates and shows state; everything else is delegated to best-of-breed tools via plugins.
4. **No lock-in.** Every external system behind a step can be swapped without changing the core or the workflow's shape.
5. **The factory owner chooses.** taktloop doesn't prescribe tools.
6. **Structure by default, freedom on demand.** A sensible default workflow comes first; breaking out of it is easy and AI-assisted.
7. **Integrations must be cheap.** If an integration is costly to build or maintain, taktloop loses its reason to exist (see §7).
8. **LLM only where it adds something.** Steps and orchestrator operations that need no judgment run as plain code.
9. **Strict, explicit step contracts.** Each step component has a fully described input/output contract with static required fields. The contracts are designed deliberately, not inferred by AI.
10. **Disposable environments.** Work that executes code runs in short-lived, isolated containers/VMs.

## 7. Executing steps and keeping integrations cheap

taktloop's value depends on integrations being cheap to build and keep working. Since [ADR 0002](adr/0002-external-tools-via-their-own-mcp-servers.md), taktloop writes no adapters: every tool is reached through an existing MCP server. What's left is deciding **who executes a step**: an agent, or plain code.

### 7.1 Agent steps and code steps

| | **Agent step** | **Code step** |
| --- | --- | --- |
| When | the step needs judgment or interaction: design, plan, build, review, triage | the step is mechanical: trigger a CI run or deployment, create a branch, mirror a status into the ticket system, send a notification |
| Executed by | an LLM agent in a harness, with the step's skills and MCP servers | the taktloop orchestrator itself, calling an MCP tool directly with no LLM |
| Defined as | a **skill** (plus the MCP servers it may use) | a **declared MCP call**: server, tool, arguments, expected result |
| Typical dispatch | pull (a harness asks for it) or push | push (the orchestrator runs it when the step opens) |
| Cost and behavior | slower, costs tokens, not fully predictable | fast, free, deterministic, testable |

A code step uses the same mechanism as a hard-gate check (ADR 0002): a declared MCP call whose structured result is checked by plain code. For example:

```yaml
step: trigger_ci
role: releaser
executor: code
call:
  mcp_server: github
  tool: run_workflow
  args: {workflow: ci.yml, ref: "{{task.branch}}"}
  expect: {status: queued}
outcome: {run_id: "{{result.id}}"}
```

**Rule of thumb:** if a step needs no judgment, make it a code step. Use an LLM only where it adds something (KISS, ADR 0004).

**Code steps stay single calls.** A code step is one MCP call, or a very short fixed sequence, with a checked result. As soon as a step needs branching, loops or error-handling logic, it's either an agent step or belongs in the tool itself (for example a CI pipeline). Otherwise taktloop would gradually turn into a general workflow engine, which is explicitly out of scope (§5).

**Code steps are mostly pushed.** A script has no session that could pull work, so the orchestrator runs a code step when it opens. Push and code steps are still separate decisions: an agent step can be pushed too (for example starting a headless reviewer).

### 7.2 What an integration costs

- **A tool:** an existing MCP server for it. taktloop can't define, implement or run missing MCP servers; they're delivered from outside the factory. That can be another factory whose product is the MCP server. Until it exists, the factory is blocked and can't be staged (ADR 0002).
- **An agent step:** a short skill describing how to do the step with that tool in this factory. AI can write and adapt it in the breakout chat.
- **A code step or gate check:** a few lines of declaration. AI can draft it from the MCP server's tool list.
- **Narrow contracts:** each step declares its output contract (required fields, artifacts). The core validates every result against it, whether it comes from an agent or from code.

### 7.3 Testing

Contract tests apply to **step outputs**, not to plugins: does a result from this executor satisfy the step's contract? Code steps and gate checks can additionally be tested against a sandbox (a test repository, a test project). **Known cost:** that sandbox setup per tool is the part AI can't fully remove.

## 8. Differentiation from existing solutions

### 8.1 Landscape

| Category | Examples | Relation to taktloop |
| --- | --- | --- |
| Integrated dev platforms | GitHub (Issues, Projects, Copilot agent, Actions), GitLab, Azure DevOps | the most complete idea-to-deploy path, inside one vendor's ecosystem. taktloop *uses* parts of them as plugins |
| CI/CD pipelines | GitHub Actions, GitLab CI, Jenkins | run builds and deployments. taktloop triggers them and reads their outcome |
| Issue trackers | Jira, Linear | hold the state of work items, kept up by hand. taktloop mirrors state into them |
| Coding agents | Claude Code, Copilot CLI/agent, Codex, Devin | do the implementation work. taktloop dispatches to them |
| Internal developer portals | Backstage, Port, Humanitec | heavy central portals with catalogs, scorecards and templates. **Not** what taktloop wants to be |
| Agent platforms | Warp (Oz), internal platforms like Webflow's (see `idea/`) | run and govern agents; heavier and usually tied to their own harness |
| General workflow engines | n8n, Temporal, Airflow | any workflow; durable execution (explicitly not taktloop's target) |

### 8.2 Where taktloop differs [proposal]

- **Lean glue, not a platform.** It adds only orchestration, state and visibility on top of the tools a team already uses or is required to use.
- **Vendor-neutral by design.** The factory owner picks each tool. Integrated platforms are best inside their own ecosystem.
- **Near-zero integration cost.** No adapters: tools are reached through their existing MCP servers. Agent steps need a short skill, and code steps a few lines of declaration, both draftable by AI (see §7).
- **The unit is the capability across its whole life,** which can span days, many tools and human approvals, not one pipeline run. That includes the phases **before the PR** (idea, spec, review).
- **Breaking out is a feature.** Changing a workflow or adding a plugin is an AI-assisted chat action, not a platform-team ticket.
- **One human-readable view** of where every task stands, across all those tools.

### 8.3 Assumptions to challenge

| Assumption | Arguments for | Arguments against / what it implies |
| --- | --- | --- |
| Enterprises are forced to mix systems (for example Jira over GitHub Issues) | Jira, ServiceNow and Azure DevOps Boards are deeply entrenched and often mandated | The common mix is usually "tracker A + code platform B", not wild variety. taktloop has to bridge a few mandated systems well; it doesn't need to support everything |
| Avoiding lock-in requires abstracting every component | Components really are changing quickly | Abstraction has a cost, and **taktloop itself becomes the thing you're locked into.** What changes fast are coding agents, harnesses and models (months). Trackers, git hosting and CI change slowly (years). Contracts must stay narrow so that the lock-in to taktloop stays small |
| A central solution saves money | Duplicated DevOps setups per unit are real costs | Central platforms fail when they're too rigid or too loose. The value depends on the default-vs-breakout balance |
| Factory-local plugins are fine | Every factory's stack is different; local changes need no coordination | If ten factories each build their own "deploy to Azure" plugin, the duplication from 2.3 returns, just made cheaper by AI. Decided: post-MVP, breakouts are promoted by pull request to the organization's template repository (§3) |

## 9. Open questions

- [x] MVP: which first factory? **taktloop building itself** (dogfooding): this repository, an existing harness in the work roles, @Tobias as requester and approver, local stand-ins for external services. Its default workflow, aligned with the superpowers skills, is in [mvp-workflow.md](mvp-workflow.md).
- [x] MVP: which identity provider to test RBAC against? The tiny built-in development issuer, a separate component behind standard OAuth/OIDC interfaces (ADR 0003).
- [x] Is the duplication of factory-local plugins acceptable long-term? Common step pieces ship with taktloop's templates; post-MVP, a factory's own breakouts are promoted by pull request to the organization's template repository (§3).
- [x] Where do default workflows come from? taktloop ships ready-made templates.
- [x] **Local minimal services vs. best-of-breed tools.** Decided in ADR 0002: MVP stand-ins; external tools are used directly through their own MCP server plus a skill.
- [x] List of basic services and lightweight open-source libraries: **deferred to the design and plan phase**, since it depends on implementation choices such as the language. The PRD only sets the constraints (lightweight budget, §5); the list is proposed there and chosen by @Tobias.

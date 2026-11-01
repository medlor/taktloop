# ADR 0001: Agent-driven orchestration through scoped MCP tools

- Status: Proposed
- Date: 2026-10-31
- Deciders: @Tobias

> Text marked **[proposal]** is a suggestion that hasn't been agreed yet. **[open]** marks a question still to answer.

## Context

taktloop orchestrates software factories (see `docs/prd.md`). Events arrive from many sources: people, tools, timers and other agents. Something has to interpret each event and move the task forward.

The earlier architecture proposal (`docs/idea/software_factory_core_proposal.md`) put a deterministic statechart engine at the center, with AI only choosing among allowed transitions at defined points. The product direction has since moved to an **AI-first, goal-driven** model: an agent, not a hand-written engine, decides what to do next.

## Decision

1. **An AI agent is the orchestrator.** It watches all incoming events and acts on them in a **goal-driven** way. Its goal and behavior are defined by a prompt, not by hand-written control flow. *Refined by [ADR 0004](0004-executors-pull-steps-via-bridge-skill.md): the core determines open steps deterministically, and executors pull them. The orchestrator becomes the **taktloop orchestrator**, a core component that manages events and task transitions and uses an AI agent only for operations that need judgment.*
2. **The agent acts only through MCP tools.** Every effect it has on the world, such as reading or updating a task or starting a step, is an MCP tool call.
3. **Tools and skills are scoped per situation.** Depending on the event and the current workflow step, the agent gets a specific set of MCP servers/tools and skills, and nothing more.
4. **MCP servers are local and minimal.** taktloop ships its own local MCP servers, each offering only the **most basic** operations and information a workflow needs. Where a lightweight open-source library provides such a basic service (for example a ticket store), it is used instead of writing one from scratch. Which services and libraries to use is decided later (see "Open"). *Refined by [ADR 0002](0002-external-tools-via-their-own-mcp-servers.md): these local services are MVP stand-ins, and external tools are used directly through their own MCP servers plus a skill.*
5. **taktloop is reachable through standard agent interfaces:** MCP, A2A and a plain API. People, harnesses and other agents interact with a factory through these.

## Guardrail [proposal]

If the agent drives everything, a manipulated or mistaken agent must still not be able to skip a hard gate (for example "deploy only with green CI" or "human approval before release"). Untrusted text in tickets, PR comments and tool output can contain prompt injections.

Proposal: **the agent decides, the tools enforce.** The MCP tools that change a task's state are deterministic and check the factory's workflow rules themselves. A call such as `advance_task(to: deploying)` is rejected unless the workflow allows that move from the current step and its required evidence (for example a recorded green CI result) exists. The agent is free in *how* it reaches the goal. The tools guarantee it can't reach a forbidden state.

This keeps the strongest point of the earlier proposal (hard gates that no model output can bypass) inside the new agent-driven model.

## Consequences

**Positive**

- Flows adapt to each situation without hand-coding every path; changing behavior means changing a prompt, a workflow rule or a tool set.
- Scoping tools per step keeps the agent focused, lowers token cost and limits the damage a mistake can do.
- Local, minimal MCP servers keep taktloop independent of external vendors and make the MVP self-contained.
- Standard agent protocols let any harness or agent work with taktloop without custom integration.

**Negative / risks**

- Non-determinism: the same event may lead to different actions. Every agent decision should be recorded (inputs, tool calls, rationale, model and prompt version) for audit and offline evaluation.
- Cost and latency: every event goes through a model. Small, fast models may be enough for routine events. *Largely removed by ADR 0004: only operations that need judgment use a model.*
- Gate safety depends entirely on the tools enforcing the rules (see Guardrail).
- Building minimal local services creates some of the "own implementation" taktloop wants to avoid (see Open).

## Alternatives considered

- **Deterministic statechart engine with AI at choice points** (the earlier proposal). Predictable and replayable, but every flow has to be modelled up front, which is at odds with highly dynamic, per-factory flows.
- **Agent with direct access to external tools' MCP servers** (Jira, GitHub, …). Least own code, but it exposes their full tool surface to the agent, and gates can't be enforced if they depend on external state. *Later adopted by [ADR 0002](0002-external-tools-via-their-own-mcp-servers.md) for external tools, with taktloop's own core MCP server keeping workflow state and gates.*

## Open

- [x] Is the agent one long-running orchestrator per factory, or one agent run per event/task? Neither: since ADR 0004 the taktloop orchestrator is a core component, and an AI agent is started only for a single operation that needs judgment.
- [x] The list of basic services (ticket store, …) and lightweight open-source libraries: deferred to the design and plan phase; proposed there, chosen by @Tobias.
- [x] How the local minimal services relate to external tools a factory owner may be required to use (for example a mandated Jira). Decided in [ADR 0002](0002-external-tools-via-their-own-mcp-servers.md).

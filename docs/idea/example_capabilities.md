# Example capabilities: an agent-platform requirements list

Source: `example_capabilities.jpg`, a photo of a screen showing a long bulleted list of capabilities in several columns. The terms "Webflow v1 tool set", "Webflow monorepo" and "Webflow-owned data" show it is a requirements list from Webflow's internal agent platform.

## Notes on the photo

- The list is laid out in four columns, shown at decreasing zoom from left to right. The text is cut off at the top, the bottom and the right edge.
- Many items appear more than once (for example "Per-run cost visibility", "Isolated per-run sandbox", "Model-agnostic dispatch"). The columns seem to be overlapping or zoomed views of one continuous list, not separate categories.
- Words cut off at the edge are completed in square brackets. Readings that are still uncertain are marked "(?)".

## Verbatim transcription

**Column 1**

- Slack-initiated agent runs
- GitHub-initiated agent iteration
- CI / event-driven triggers
- Scheduled triggers
- IDE-initiated agent handoff
- Surface-agnostic webhook contract
- Unified inbound API
- Intent classification & routing
- Persistent job state
- Initiator + approver audit trail
- Branch protection compatible with agents

**Column 2**

- Model-agnostic dispatch
- Thread/run continuity
- Freestyle / open-ended invocation
- Multi-step workflow sequencing
- Durable execution
- Human-in-the-loop gates
- Hierarchical sub-agents
- Bounded retry & rollback
- Workflow versioning
- Network egress controls
- Per-run resource limits
- Per-user and per-workflow cost guardrails
- Allowlist + stage-based access
- Per-run cost visibility
- Operational alerting
- Quality, throughput, and request-resolution metrics
- Per-run cost visibility
- Isolated per-run sandbox
- Production-like stack
- Fast spin-up
- Non-human identity for agents
- Pluggable execution backend

**Column 3**

- Reusable skill / command library
- Skill discoverability & catalog
- Specialized sub-agent definitions
- Portability across IDEs and remote runners
- Selective rule / skill activation
- PR-review pattern extraction
- Retrieval-based knowledge (RAG)
- Service-swap topology
- Governed MCP gateway
- Service-level credentials (not user OAuth)
- Secrets handling
- Run tracking and history
- System-level eval harness
- Outcome capture
- User satisfaction signal
- Webflow internal tool coverage
- Isolated per-run sandbox
- Production-like stack
- Fast spin-up
- Non-human identity for agents
- Pluggable execution backend
- Tool extensibility model
- Per-workflow tool scoping
- Webflow v1 tool set
- Audit trail per tool call…
- "What's running right now" view
- Quality, throughput, and … (cut off)

**Column 4** (smallest text, right edge cut off)

- … agents (continuation of an item above the photo)
- Isolated per-run sandbox
- Domain ownership & contributi[on] model
- Rule and skill evals
- Externalized, Webflow-owned d[ata] and memory layer
- Webflow monorepo compatibi[lity]
- DDA migration as a beneficiary, [not a(?)] blocker
- Non-transferable Webflow conte[xt] (context engineering)…
- Webflow v1 tool set
- Audit trail per tool call…
- "What's running right now" view
- Quality, throughput, and request-resolution metrics
- Per-run cost visibility
- Operational alerting
- Learning feedback into context
- Model-agnostic dispatch
- Thread/run continuity
- Freestyle / open-ended invocation
- Multi-step workflow sequencing
- Durable execution
- Human-in-the-loop gates
- Hierarchical sub-agents
- Bounded retry & rollback
- Workflow versioning
- Network egress controls
- Per-run resource limits
- Per-user and per-workflow cost guardrails
- Allowlist + stage-based access
- Rule and skill evals
- Externalized, Webflow-owned da[ta] and memory layer

## Deduplicated capabilities, grouped by theme

The grouping is an interpretation; the photo doesn't contain it. Company-specific items are generalized in parentheses.

**1. Triggers and intake:** how work enters
- Slack-initiated agent runs
- GitHub-initiated agent iteration
- CI / event-driven triggers
- Scheduled triggers
- IDE-initiated agent handoff
- Freestyle / open-ended invocation
- Surface-agnostic webhook contract
- Unified inbound API
- Intent classification & routing

**2. Orchestration and execution semantics:** how work runs
- Persistent job state
- Durable execution
- Thread/run continuity
- Multi-step workflow sequencing
- Hierarchical sub-agents
- Specialized sub-agent definitions
- Human-in-the-loop gates
- Bounded retry & rollback
- Workflow versioning
- Model-agnostic dispatch

**3. Execution environment:** where work runs
- Isolated per-run sandbox
- Production-like stack
- Fast spin-up
- Pluggable execution backend
- Portability across IDEs and remote runners
- Service-swap topology
- Per-run resource limits
- Network egress controls

**4. Identity, access and security**
- Non-human identity for agents
- Service-level credentials (not user OAuth)
- Secrets handling
- Allowlist + stage-based access
- Branch protection compatible with agents
- Governed MCP gateway
- Per-workflow tool scoping

**5. Tools, skills and knowledge**
- Reusable skill / command library
- Skill discoverability & catalog
- Selective rule / skill activation
- Tool extensibility model
- Internal tool coverage / a v1 tool set (Webflow-specific)
- Retrieval-based knowledge (RAG)
- PR-review pattern extraction
- Externalized, organization-owned data and memory layer
- Organization-specific context that isn't transferable from generic tools (context engineering)
- Learning feedback into context

**6. Audit, observability and cost**
- Initiator + approver audit trail
- Audit trail per tool call
- Run tracking and history
- "What's running right now" view
- Operational alerting
- Per-run cost visibility
- Per-user and per-workflow cost guardrails
- Quality, throughput, and request-resolution metrics

**7. Quality and evaluation**
- System-level eval harness
- Rule and skill evals
- Outcome capture
- User satisfaction signal

**8. Organizational fit**
- Domain ownership & contribution model
- Monorepo compatibility
- An ongoing migration ("DDA") treated as a beneficiary, not a blocker

## What it means

The list is a requirements checklist for making AI agents a governed part of engineering, not ad-hoc tools on individual laptops. It covers four concerns:

1. **Work can start from anywhere** (chat, GitHub, CI, schedules, the IDE). Everything goes through one surface-agnostic inbound contract and is classified and routed by intent.
2. **Runs are durable and controlled.** Multi-step workflows keep their state, can be resumed and retried within bounds, have human approval gates, and are versioned.
3. **Agents are safe to let loose.** Each run is sandboxed, has its own non-human identity and service credentials, has limited network access and scoped tools, and every tool call is audited.
4. **The organization learns and stays in control.** Skills, rules and knowledge live in a shared, organization-owned layer. Outcomes, costs and quality are measured and evaluated, and that feedback improves the context agents work with.

## How this maps to the taktloop proposal

This mapping is an interpretation; the photo doesn't contain it.

| Theme | Covered by the proposal | Not yet covered |
| --- | --- | --- |
| Triggers and intake | `Ingress` port, `ai-ingress`, schedule/count/manual triggers, CLI/API | Slack/IDE handoff (only as future plugins) |
| Orchestration | statecharts, `decide`, durable event log, hard gates, `attempts_below`, chart versioning, model-agnostic executors | rollback; explicit sub-agent hierarchy |
| Execution environment | out-of-process executors in sandboxed containers, egress limits | resource limits, fast spin-up, production-like stack |
| Identity and security | per-action tokens, credentials in plugins, scoped PATs | non-human identity model, governed MCP gateway, per-workflow tool scoping |
| Tools, skills, knowledge | prompts as versioned config | skill library/catalog, RAG, shared memory, learning feedback |
| Audit and cost | event log as audit trail, `usage` on outcomes, `factory inspect`, OTel | per-tool-call audit, cost guardrails (limits, not only visibility), alerting |
| Quality | decision events re-runnable offline | eval harness, outcome capture, satisfaction signal |
| Organizational fit | n/a (single-user / NUC scope) | ownership and contribution model |

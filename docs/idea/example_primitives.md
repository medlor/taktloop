# Example primitives: the factory primitives

Source: `example_primitives.jpg`, a photo of a presentation slide titled **"The factory primitives:"** (slide number 48). The product names on it (Oz Web, Warp Agent, Warp Drive, Warp Hosted Cloud) suggest it comes from a Warp talk. Some of the small labels are blurry; uncertain readings are marked with "(?)".

## What the slide shows

A layered architecture for an agent factory: four stacked layers, joined by dotted lines labelled with the plane between them.

## Transcription

**Surfaces (top layer):** the ways people reach the factory.

| Surface | Subtitle |
| --- | --- |
| Terminal | Agentic Development Environment |
| Oz Web | Agent dashboard |
| Integrations (Slack, Linear(?), GitHub icons) | Agent dashboard |
| CLI/API/SDK | Agent dashboard |
| Mobile | Agent dashboard |

**Agent Control Plane:**

| Agent Configuration | Orchestration | Administration |
| --- | --- | --- |
| Skills | Smart Agent Routing | Cost Controls |
| Model / Harness | Rules | Governance |
| Environments | | Billing |
| Tools / MCP | | User Management |
| Permissions | | Evals |
| Memory | | |

**Agent Execution:**

| Hosting & Deployment | Multi-harness | Multi-agent |
| --- | --- | --- |
| Warp Hosted Cloud | Warp Agent | Orchestrator Agent |
| Self-hosted Infra | Claude Code | Validator Agent |
| | Codex | Builder Agent |

**Agent Data Plane:**

- Artifacts
- Warp Drive
- Skills
- Memory
- Knowledge Bases
- Rules

## What it means

- **Many surfaces, one factory.** Terminal, web dashboard, chat and tracker integrations, CLI/API/SDK and mobile are all entry points to the same system. None of them is the system.
- **Control plane:** decides *what* runs and *under which rules*:
  - *Configuration:* which skills, model/harness, environment, tools/MCP servers, permissions and memory an agent gets.
  - *Orchestration:* routing a request to the right agent ("smart agent routing"), constrained by rules.
  - *Administration:* cost controls, governance, billing, user management, and evals that measure agent quality.
- **Execution:** *where* and *with what* the agent runs:
  - Hosted or self-hosted infrastructure.
  - **Multi-harness:** the same factory drives different coding agents (Warp Agent, Claude Code, Codex), so the harness is interchangeable.
  - **Multi-agent:** agents have roles. An orchestrator delegates, a builder produces, and a validator checks.
- **Data plane:** the shared, persistent knowledge every run draws on and adds to: artifacts, skills, rules, memory and knowledge bases, plus Warp Drive as the product's own shared store.

The idea behind the slide is that control, execution and data are separate layers. The harness is a pluggable part of the execution layer, and knowledge (skills, rules, memory) is held centrally rather than inside any one agent.

## How this maps to the taktloop proposal

This mapping is an interpretation; the slide doesn't contain it.

| Slide layer | Proposal concept | Gap / note |
| --- | --- | --- |
| Surfaces | CLI + HTTP API (v1), MCP (Phase 3), UI (Phase 4); `Ingress` plugins for integrations | no mobile; the proposal treats the UI as a later read view |
| Control plane: orchestration, rules | charts (statecharts) + `decide` + guards; AI `choose:` | "smart agent routing" ≈ `triage` / `decide_next` |
| Control plane: configuration | bindings (`*.bind.yaml`) mapping capabilities to plugins with `with:` config | skills, memory and permissions per agent aren't modelled |
| Control plane: administration | cost `usage` on outcomes, security section, decision events for offline evals | no billing, user management or governance in v1 (single-user scope) |
| Execution: multi-harness | `Executor` port; `claude-code-headless`, `copilot-cli-headless`, out-of-process plugins | matches the proposal's "who executes is invisible" principle |
| Execution: hosting | the NUC vs. enterprise open decision; sandboxed containers | |
| Execution: multi-agent roles | separate capabilities (`implement`, review, verify) bound to different executors | the proposal has no orchestrator-agent concept; the chart is the orchestrator |
| Data plane | `ArtifactStore` port + `ArtifactRef`; prompts as versioned config | no shared skills/rules/memory/knowledge-base layer yet |

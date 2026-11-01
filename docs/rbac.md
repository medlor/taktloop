# taktloop — Roles and access control (RBAC)

Status: draft · Nov 1, 2026 · @Tobias

> Text marked **[proposal]** is a suggestion that hasn't been agreed yet. **[open]** marks a question still to answer.

## 1. Why roles

Every step of a factory's workflow is done by someone: a person, an agent in a harness, or a script. taktloop has to know **who may do what**, for every action and for humans and agents alike, without caring *where* the work runs. Roles are how it knows.

## 2. Three separate concepts

| Concept | Answers | Example |
| --- | --- | --- |
| **Role** | *what* may be done: a responsibility plus a set of permissions in the core | "builder: may submit `implementation.done` for a task; may not approve" |
| **Executor** | *who / where* it runs | Claude Code, Copilot CLI, Codex, a script, a person |
| **Binding** | per factory: which executor fills which role | factory A: builder → Claude Code; factory B: builder → Copilot CLI |

Each workflow step is assigned to a role. A factory's binding decides which executor fills it. Using Claude Code for every role is a valid binding, but taktloop never requires it.

## 3. Roles

### 3.1 Work roles (usually agents)

| Role | Responsibility | Permissions in the core |
| --- | --- | --- |
| **Orchestrator** | the taktloop orchestrator: manages events and task transitions; pushes steps that need pushing (starts headless runs, notifies people); escalates stalls and out-of-sync events. Uses AI only where needed (ADR 0004) | read state and the audit log; start or notify executors; flag tasks; **can't do or approve work** |
| **Triage** | classifies new requests: automatable, needs spec, needs clarification, park | set a task's route and priority |
| **Planner** | turns an idea into a spec and plan | submit spec/plan artifacts for a task |
| **Builder** | implements | submit implementation results (branch, commit, artifacts) for a task |
| **Reviewer** | reviews the builder's output | submit a review verdict |
| **Verifier** | runs and judges tests and checks | submit verification results |
| **Releaser** | triggers CI/CD and deployment | start release/deploy steps, only after their gates pass |
| **Monitor** | watches production and reports problems | create new tasks (closes the loop) |

### 3.2 Human roles

| Role | Responsibility | Permissions in the core |
| --- | --- | --- |
| **Requester** | submits ideas, answers clarification questions, accepts results | create tasks; submit clarification and acceptance events |
| **Approver** | holds human gates (spec approval, release sign-off) | submit approval/rejection events at gates |
| **Factory owner** | configures workflows, bindings and plugins; approves breakouts | change factory configuration |
| **Viewer** | follows progress | read state |
| **Auditor** | checks what happened and why | read state and the full audit log |

### 3.3 Special roles

| Role | Responsibility | Permissions in the core |
| --- | --- | --- |
| **Factory assistant** | the breakout-chat agent: drafts skills, plugins and workflow changes | *propose* configuration changes; they take effect only after a factory owner approves them |
| **Integration** | webhooks, CI callbacks, other systems | submit events of the types declared for it; nothing else |

Roles are a default set. A factory may rename, merge or add roles, for example one combined "builder + verifier" role for a small team, as long as the separation rules below still hold.

## 4. Rules

1. **Every operation is checked.** Every call to the core MCP server, whether it reads state, submits an event, approves a gate or changes configuration, is authorized against the caller's role. This applies to the dashboard, CLI, API, A2A and MCP alike.
2. **Least privilege.** A role gets only the operations its responsibility needs.
3. **Separation of duties per task.** One identity can't hold conflicting roles on the same task. By default: builder ≠ reviewer, builder ≠ approver, and requester ≠ approver for release gates. The core enforces this from its own records, for example "this identity submitted the implementation for task 42, so it may not approve task 42".
4. **The orchestrator dispatches, it doesn't work.** It can't submit results of agent or human steps, and it can't approve. **[proposal]** A code step it runs (PRD §7) is recorded under the step's own role (for example releaser) with executor `code`, so separation of duties still applies to it.
5. **Gates are role-bound.** A human gate names the role that may pass it (for example `approver`). A verified gate is checked by plain code (ADR 0002).
6. **Everything is audited.** Every operation is logged with the caller's identity, role, task and outcome. Denied calls are logged too.

## 5. Identity and tokens

**Decided: in production, taktloop doesn't mint tokens.** Issuing and managing identities and credentials is the job of the organization's existing identity provider (Entra ID, Okta, Keycloak, …). That's complex, already in place in enterprises, and not KISS for taktloop to rebuild.

**Decided: for development and the MVP, a tiny built-in development issuer** may issue tokens, so taktloop can be tried without an identity provider. It is a separate component with a clear boundary: standard OAuth/OIDC endpoints only, and the core treats it like any external identity provider. It is not used in production. See [ADR 0003](adr/0003-rbac-with-external-oauth-tokens.md).

**Decided: the core MCP server validates external tokens on every operation.** It acts as an OAuth 2.1 resource server, as the MCP authorization specification describes:

1. Every caller (human, agent, integration) presents an access token issued by the organization's identity provider.
2. The core validates the token: signature (via the provider's published keys) or token introspection, issuer, audience (the token must be meant for this taktloop instance), and expiry.
3. The core reads the caller's **roles from the token's scopes** (post-MVP optionally from a groups claim, see below) and its **identity from the subject**.
4. The core authorizes the operation against those roles, then applies the per-task rules (separation of duties, gates) from its own records.

**Agents need their own identities.** An agent run uses a non-human identity from the identity provider (a service principal / workload identity), not a developer's personal account. Creating those identities is the organization's job; taktloop only validates and authorizes them.

**Decided: scope naming.** By default, roles come from scopes of the form `taktloop:<factory>:<role>`, for example `taktloop:shop:builder`. This is also what the dev issuer issues. Post-MVP, a group-level grant could look like `taktloop:group:<group>:<role>`.

**Post-MVP: mapping existing group claims.** Not needed yet; the MVP reads roles from scopes only. Later, as an alternative to scopes, a factory can read roles from a claim its identity provider already sends (for example `groups`) and map those values to roles, so an enterprise can reuse existing groups instead of creating new scopes:

```yaml
auth:
  issuer: https://login.example.com
  audience: taktloop
  roles_from: scope            # default; or: claim "groups"
  map:                         # only needed for claims
    sg-dev-shop-builders: builder
    sg-dev-shop-approvers: approver
```

## 6. Where roles apply

**Decided:**

- **MVP: roles are scoped per factory instance.** Being `builder` in one factory gives no rights in another. There is no finer scoping inside a factory (for example per product area).
- **Post-MVP: tenants.** Factories belong to a tenant, and tenants are isolated from each other.
- **Post-MVP: factory groups.** Factories can be grouped (for example per department). A role granted on a group applies to all its factories, so a department's approvers or auditors are managed once instead of per factory. This keeps management simple as the number of factories grows.

Resolution order (post-MVP): tenant → factory group → factory. A role granted at a higher level applies below it; separation of duties is still checked per task.

## 7. Limits: what taktloop does not control

taktloop's RBAC covers **actions through the core**. It doesn't control what an executor can do directly in external tools. A builder with a broad GitHub token can still merge a PR without going through the core.

That's deliberate: credentials for external tools are scoped through the organization's IAM and the factory's setup, not by taktloop. The core still protects what it owns: a task's state can only move through the core, its gates hold, and a change made outside the core doesn't advance a task. **[proposal]** taktloop's documentation should recommend scoping external credentials per role (for example a builder token that can push branches but not merge). The required scopes per role come from each plugin's skill (ADR 0002).

## 8. Open

- [x] How the MVP gets tokens without an enterprise IAM: through the built-in development issuer (ADR 0003).
- [x] Final scope naming: `taktloop:<factory>:<role>` is the default and what the dev issuer issues.
- [x] Mapping existing group claims to roles: not in the MVP; added when the first enterprise identity provider is connected.
- [x] Whether roles can be scoped to parts of a factory: no. Per factory instance in the MVP; tenants and factory groups later (§6).

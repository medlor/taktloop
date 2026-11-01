# ADR 0003: RBAC in the core, validated from external OAuth tokens

- Status: Proposed
- Date: 2026-11-01
- Deciders: @Tobias

## Context

Every workflow step is assigned to a role, and roles are filled by humans, agents or scripts (see `docs/rbac.md`). The core MCP server is the single source of truth for task state, workflow and gates, so it has to know who is calling and what that caller may do, on every operation.

Someone has to issue identities and credentials. taktloop could issue them itself (for example short-lived, role-scoped tokens per run), or rely on the organization's existing identity provider.

## Decision

1. **In production, taktloop doesn't mint or manage tokens** (for development and the MVP, see 5). Identities and credentials come from the organization's identity provider (Entra ID, Okta, Keycloak, …). Teams and enterprises already run one, and rebuilding that would contradict KISS.
2. **The core MCP server validates external tokens on every operation.** It acts as an OAuth 2.1 resource server: it checks signature or introspection, issuer, audience and expiry, then reads the caller's roles from the token's scopes (default form `taktloop:<factory>:<role>`; mapping a groups claim is post-MVP, see `rbac.md` §5) and its identity from the subject.
3. **Authorization happens in the core.** Each operation (read state, submit event, approve gate, change configuration) is checked against the caller's roles. Per-task rules such as separation of duties are checked against the core's own records.
4. **Agents use non-human identities** from the identity provider, never a developer's personal account.
5. **Exception for development and the MVP: a tiny built-in development issuer.** So that taktloop can be tried in minutes without running an identity provider, taktloop ships a minimal token issuer for development and the MVP. It may issue tokens. It is **not** used in production, where the organization's identity provider takes its place.
6. **The development issuer is a separate component with a clear boundary.** It talks to the rest of taktloop only through standard OAuth/OIDC interfaces: a token endpoint, published signing keys (JWKS) and discovery metadata. The core validates its tokens exactly like those of any external identity provider; the only difference is the configured issuer URL. The core contains no issuer code and has no special path for development tokens. Removing the development issuer, or replacing it with Entra ID, Okta or Keycloak, is a configuration change only.

## Consequences

**Positive**

- Small, standard and KISS: token validation is a well-supported library task, with no credential issuing, rotation or storage in taktloop.
- Fits enterprise IAM already in place, so central access policies, joiners/leavers and audits keep working.
- Matches the MCP authorization specification, so standard MCP clients can authenticate.

**Negative / risks**

- taktloop depends on the identity provider issuing suitable scopes or claims; the claim-to-role mapping must be flexible.
- Per-task restrictions (for example "builder only for task 42") can't be expressed in the token; they live in the core's records.
- taktloop doesn't control credentials for external tools. A broadly scoped external token can bypass the core for direct tool actions, although not for task state or gates.
- The development issuer is extra code to keep small and secure enough for local use. It must never be enabled by accident in production: it should be off by default, with a clear warning when it runs.

## Alternatives considered

- **taktloop as a credential broker,** issuing short-lived, role- and task-scoped tokens for the core and for external tools. Tightest control, but complex, security-critical and duplicating enterprise IAM. Rejected.
- **Shared API keys per role.** Simple, but no individual identity, which makes separation of duties and audit impossible. Rejected.

# M2: Roles, Tokens and Plan Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Every core call is authorized from a dev-issuer token for a named identity, a `plan_review` step reviews plans (with escalation and approver override), and an opt-in `strict` mode enforces separation of duties.

**Architecture:** A separate dev issuer (`src/taktloop/issuer/`, own process, client-credentials grant, ES256 JWTs, JWKS) that the core only reaches over HTTP. The core plugs a JWT `TokenVerifier` into the MCP SDK's resource-server support; `caller_from` builds the `Caller` from the validated token; `service` checks a permissions table and (in strict mode) pure separation rules before every operation. The workflow loader learns review steps and multi-target rejects.

**Tech Stack:** Python ≥ 3.12, uv, `mcp` 2.3 (`MCPServer`, `TokenVerifier`, `AuthSettings`, `get_access_token`), `pyjwt[crypto]`, `pyyaml`, SQLite, pytest (+ anyio). HTTP in tests via `httpx2` (installed through `mcp`; plain `httpx` and `starlette.testclient` are **not** available).

**Spec:** `docs/superpowers/specs/2026-11-02-m2-roles-and-separation-design.md` (builds on `docs/superpowers/specs/2026-11-01-mvp-milestones-and-m1-design.md`).

## Global Constraints

- Python ≥ 3.12; uv for everything (`uv run pytest`, `uv run taktloop …`, `uv add`).
- Direct runtime dependencies exactly: `mcp>=2.3`, `pyyaml`, `pyjwt[crypto]`. Nothing else new.
- **≤ ~2,200 non-test lines** in `src/` in total (core + CLI + issuer); no module over ~300 lines.
- No module outside `src/taktloop/issuer/` imports from it; `issuer/` imports nothing from the rest of `taktloop`. The core reaches the issuer over HTTP only.
- No unauthenticated mode in the core: `auth` is required in `factory.yaml`.
- Scopes have the form `taktloop:<factory>:<role>`; other factories' scopes are ignored.
- Dev tokens: ES256 JWT, claims `iss`, `sub`, `aud`, `iat`, `exp`, `scope` (space-separated), `client_id`; default lifetime `12h`.
- Defaults: core `127.0.0.1:8765`, issuer `127.0.0.1:8766`, audience `taktloop`, `separation: off`.
- `.taktloop/` stays gitignored (keys, secret hashes, credentials, database).
- Docs in English; product name always "taktloop".
- Existing behaviour from M1 stays unless a task changes it; all M1 tests keep passing (adapted only where a task says so).

## Review Focus

1. **Token for another factory or audience**: a valid dev token whose scopes are all `taktloop:other:*` must give the caller no roles (every non-read call refused, reads refused too). Test in Task 4.
2. **Escalation gate sends the task back, then review fails again**: the issue count restarts after leaving the escalation gate, so the 1st new `issues` reopens `plan` and the 2nd escalates again. Test in Task 6.
3. **Override while a plan revision is in progress**: the override approves the latest *completed* plan round and pins its artifacts, not the open round. Test in Task 7.
4. **Expired token mid-session**: the CLI prints a clear "token missing or expired" message instead of a stack trace when the core answers 401. Test in Task 3.
5. **M1 database on disk**: starting the core on a schema-v1 database fails with a message telling the user to delete `.taktloop/factory.db`. Test in Task 5.

---

### Task 1: Split `service.py` (no behaviour change)

**Files:**
- Create: `src/taktloop/ops_base.py`, `src/taktloop/ops_steps.py`, `src/taktloop/ops_gates.py`
- Modify: `src/taktloop/service.py`

**Interfaces:**
- Produces: `taktloop.ops_base`: `Caller`, `_Ids`, `_utc_now`, class `ServiceBase` (holds `__init__`, `_event`, `_run`, `_expire`, `_load`, `_load_owned`, `_summary`, `_open_step`, `_artifacts`, `_claim`). `taktloop.ops_steps.StepOps(ServiceBase)`: `claim_step`, `heartbeat`, `complete_step`, `release_step`. `taktloop.ops_gates.GateOps(ServiceBase)`: `decide_gate`. `taktloop.service.Service(StepOps, GateOps)`: `create_task`, `list_open_steps`, `get_step_context`, `get_task`, `list_tasks`; `taktloop.service` re-exports `Caller` and `Rejected` (tests and `mcp_server` import them from there).

- [ ] **Step 1: Move code** as listed above. Method bodies are moved verbatim; only imports change.
- [ ] **Step 2: Run the full suite**

Run: `uv run pytest -q`
Expected: `85 passed` (same count as before), no test file changed.

- [ ] **Step 3: Check sizes**

Run: `wc -l src/taktloop/*.py`
Expected: every file ≤ 300 lines.

- [ ] **Step 4: Commit**

```bash
git add src/taktloop
git commit -m "Split service into base, step and gate operations (no behaviour change)"
```

---

### Task 2: Dev issuer

**Files:**
- Create: `src/taktloop/issuer/__init__.py`, `src/taktloop/issuer/config.py`, `src/taktloop/issuer/state.py`, `src/taktloop/issuer/app.py`
- Create: `dev-issuer.yaml` (repo root, the spec §4.2 example verbatim)
- Modify: `src/taktloop/cli.py` (commands `issuer` and `issuer init [--rotate ID]`), `pyproject.toml` (`uv add 'pyjwt[crypto]'`)
- Test: `tests/test_issuer.py`

**Interfaces:**
- Produces:
  - `issuer.config.IssuerConfig(issuer: str, audience: str, token_lifetime: timedelta, state_dir: Path, identities: dict[str, tuple[str, ...]])` (identity → scopes), `load_issuer_config(path: Path) -> IssuerConfig`. `host`/`port` come from the `issuer` URL. `token_lifetime` accepts `"<n>h"` or `"<n>m"`; `state_dir` is relative to the config file.
  - `issuer.state.init_state(cfg: IssuerConfig, credentials_dir: Path, rotate: str | None = None) -> list[str]` (identities written). Creates `state_dir/signing_key.pem` (EC P-256) if missing, `state_dir/secrets.json` (`{identity: sha256 hex of secret}`), and for every identity without a secret (or the rotated one) `credentials_dir/<identity>.json` = `{"issuer", "client_id", "client_secret"}` with mode `0o600`. Existing secrets are kept. `rotate` naming an unknown identity raises `ValueError`.
  - `issuer.app.build_app(cfg: IssuerConfig, clock: Callable[[], datetime] = …) -> Starlette` with `GET /.well-known/oauth-authorization-server`, `GET /jwks.json`, `POST /token`.
  - CLI: `taktloop issuer [--config dev-issuer.yaml]` runs it with uvicorn and prints `WARNING: taktloop dev issuer, for development only` to stderr; `taktloop issuer init [--config …] [--rotate ID] [--credentials .taktloop/credentials]` prints the identities written.

- [ ] **Step 1: Write the failing tests** (`httpx2.AsyncClient(transport=httpx2.ASGITransport(app=build_app(cfg)), base_url="http://test")`, anyio):

```python
async def test_metadata(issuer):        # issuer fixture: tmp config with agent-a + tobias, init_state run
    m = (await issuer.get("/.well-known/oauth-authorization-server")).json()
    assert m["issuer"] == "http://127.0.0.1:8766"
    assert m["token_endpoint"] == "http://127.0.0.1:8766/token"
    assert m["jwks_uri"] == "http://127.0.0.1:8766/jwks.json"
    assert m["grant_types_supported"] == ["client_credentials"]

async def test_token_carries_all_scopes(issuer, creds):
    r = await issuer.post("/token", data={"grant_type": "client_credentials",
                                          "client_id": "agent-a", "client_secret": creds["agent-a"]})
    assert r.status_code == 200 and r.json()["token_type"] == "Bearer"
    claims = jwt.decode(r.json()["access_token"], key=jwks_key(issuer), algorithms=["ES256"],
                        audience="taktloop")
    assert claims["sub"] == "agent-a" and claims["iss"] == "http://127.0.0.1:8766"
    assert claims["scope"] == "taktloop:taktloop:planner taktloop:taktloop:reviewer"
    assert claims["exp"] - claims["iat"] == 12 * 3600

async def test_basic_auth_accepted(issuer, creds): ...      # HTTP Basic client auth → 200
async def test_wrong_secret_is_invalid_client(issuer):      # 401, {"error": "invalid_client"}
async def test_unknown_identity_is_invalid_client(issuer):  # 401, {"error": "invalid_client"}
async def test_other_grant_is_unsupported(issuer, creds):   # 400, {"error": "unsupported_grant_type"}
def test_init_keeps_existing_secrets_and_rotates_one(tmp_path): # second init writes []; rotate writes ["agent-a"], new secret differs
def test_credentials_file_mode_is_0600(tmp_path):
def test_rotate_unknown_identity_raises(tmp_path):
```

- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_issuer.py -q`
Expected: FAIL (`ModuleNotFoundError: taktloop.issuer`)

- [ ] **Step 3: Implement** the interfaces above. The signing key's `kid` is the RFC 7638 JWK thumbprint. The token response is `{"access_token", "token_type": "Bearer", "expires_in"}`. Secrets: `secrets.token_urlsafe(32)`, compared with `hmac.compare_digest` on the sha256 hex.
- [ ] **Step 4: Run them to see them pass**

Run: `uv run pytest tests/test_issuer.py -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/taktloop/issuer src/taktloop/cli.py tests/test_issuer.py dev-issuer.yaml pyproject.toml uv.lock
git commit -m "Add the dev issuer: client-credentials tokens, JWKS, metadata"
```

---

### Task 3: `taktloop token` and the CLI's own identity

**Files:**
- Create: `src/taktloop/token_client.py`
- Modify: `src/taktloop/cli.py`, `src/taktloop/config.py`
- Test: `tests/test_token_client.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: credentials file format from Task 2; issuer metadata `token_endpoint`.
- Produces:
  - `token_client.fetch_token(identity: str, credentials_dir: Path = Path(".taktloop/credentials")) -> str`: reads `<identity>.json`, GETs `<issuer>/.well-known/oauth-authorization-server`, POSTs the client-credentials grant to its `token_endpoint`, returns `access_token`. Stdlib `urllib` only. Raises `TokenError(message)` on a missing file (`"no credentials for <identity> in <dir>; run taktloop issuer init"`), an unreachable issuer, or a non-200 answer.
  - `FactoryConfig.cli_identity: str | None` (from `cli.identity`).
  - CLI: `taktloop token <identity> [--credentials DIR]` prints the bare token (stdout) and exits 0, or prints the error to stderr and exits 2. Every client command gets `--as IDENTITY` and resolves its bearer token as: `TAKTLOOP_TOKEN` env var → `--as` → `cfg.cli_identity` → error `"no identity: set TAKTLOOP_TOKEN, pass --as, or set cli.identity in factory.yaml"`. With `--url` (no config read), `cli.identity` is unavailable.
  - `_call_tool(url, name, args, token: str)` sends `Authorization: Bearer <token>` via `Client(streamable_http_client(url, http_client=create_mcp_http_client(headers=…)))`. An HTTP 401 from the core becomes `"taktloop: token missing or expired; get a new one with taktloop token <identity>"`, exit 2.

- [ ] **Step 1: Write the failing tests**: `fetch_token` against a real issuer started with uvicorn in a background thread on a free port (fixture in `tests/conftest.py`, reused by Tasks 4 and 9): returns a decodable token for `agent-a`; missing credentials → `TokenError` naming the dir; wrong secret → `TokenError`. CLI: `main(["token","agent-a","--credentials",dir])` prints one line that is a JWT; the resolution order (`TAKTLOOP_TOKEN` wins over `--as`, which wins over `cli.identity`), using monkeypatched `fetch_token`; a 401 from the core (Review Focus 4) prints the message above and returns 2.
- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_token_client.py tests/test_cli.py -q`
Expected: the new tests fail.

- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass (the core still ignores the header; e2e still passes).

- [ ] **Step 5: Commit**

```bash
git add src/taktloop tests
git commit -m "Add taktloop token and bearer tokens in the CLI"
```

---

### Task 4: Core validates tokens; callers come from the token

**Files:**
- Create: `src/taktloop/auth.py`
- Modify: `src/taktloop/config.py`, `src/taktloop/mcp_server.py`, `src/taktloop/serve.py`, `factory.yaml`, `templates/mvp/factory.yaml`
- Test: `tests/test_auth.py`, `tests/test_mcp_server.py`, `tests/test_config.py`, `tests/test_e2e.py`, `tests/conftest.py`

**Interfaces:**
- Produces:
  - `FactoryConfig.auth_issuer: str`, `FactoryConfig.auth_audience: str` (from the required `auth:` block; missing → `WorkflowError("factory.yaml: 'auth' block with issuer and audience is required")`).
  - `auth.roles_from_scopes(scopes: Iterable[str], factory: str) -> frozenset[str]`.
  - `auth.JwtVerifier(issuer: str, audience: str, fetch_json: Callable[[str], dict] = <urllib GET>)` implementing `mcp.server.auth.provider.TokenVerifier`: discovers `jwks_uri` from the issuer's metadata on first use, caches keys, refetches once on an unknown `kid`; validates ES256 signature, `iss`, `aud`, `exp`; returns `AccessToken(token, client_id, scopes, expires_at, subject=sub, claims={"iss": iss})`, or `None` on any failure (and logs the reason at WARNING with logger `taktloop.auth`).
  - `mcp_server.build_server(service: Service, factory: str, verifier: TokenVerifier | None, resource_url: str | None) -> MCPServer`. With a verifier it passes `token_verifier=verifier, auth=AuthSettings(issuer_url=…, resource_server_url=resource_url, validate_token_resource=False)`. `caller_from(ctx, factory)` uses `get_access_token()`; no token → `ToolError("not authenticated")`; otherwise `Caller(subject, roles_from_scopes(scopes, factory), executor)`.
  - Test helper in `tests/conftest.py`: `as_identity(subject: str, *roles: str, factory="taktloop")`, a context manager that sets `mcp.server.auth.middleware.auth_context.auth_context_var` to `AuthenticatedUser(AccessToken(...))` around an in-memory `Client(build_server(...))` session, so in-memory tests run as a given identity.

- [ ] **Step 1: Write the failing tests**: `roles_from_scopes(["taktloop:taktloop:planner", "taktloop:other:approver", "openid"], "taktloop") == {"planner"}`; `JwtVerifier` with a key pair generated in the test and an in-memory `fetch_json`: valid token → subject and scopes; bad signature, wrong `iss`, wrong `aud`, expired → `None`; unknown `kid` triggers exactly one refetch. `load_config` without `auth` → error. In-memory MCP: with no `as_identity` → tool error `not authenticated`; with `as_identity("agent-a","planner")` → `Caller.subject == "agent-a"` reaches the history. Review Focus 1: a token whose scopes are all `taktloop:other:*` gives `roles == frozenset()`. e2e: start a real issuer and core; a raw POST to `/mcp` without `Authorization` gets HTTP 401; the existing restart test passes using tokens.
- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_auth.py tests/test_mcp_server.py tests/test_config.py -q`
Expected: the new tests fail.

- [ ] **Step 3: Implement**, and add `auth: {issuer: http://127.0.0.1:8766, audience: taktloop}` and `cli: {identity: tobias}` to both `factory.yaml` files. `serve.run` builds `JwtVerifier(cfg.auth_issuer, cfg.auth_audience)` and passes `resource_url=cfg.url`. Existing in-memory tests switch to `as_identity(...)` with every role (`requester, planner, reviewer, approver, factory_owner`).
- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/taktloop tests factory.yaml templates/mvp/factory.yaml
git commit -m "Core validates dev-issuer tokens; callers come from the token"
```

---

### Task 5: Permissions, claim ownership by identity, audit columns

**Files:**
- Create: `src/taktloop/permissions.py`
- Modify: `src/taktloop/ops_base.py`, `src/taktloop/ops_steps.py`, `src/taktloop/ops_gates.py`, `src/taktloop/service.py`, `src/taktloop/store.py`, `src/taktloop/models.py`, `src/taktloop/cli.py` (history rendering)
- Test: `tests/test_permissions.py`, `tests/test_store.py`, existing service tests

**Interfaces:**
- Produces:
  - `permissions.require(caller: Caller, operation: str, step: Step | None = None) -> None` raising `Rejected("<operation> requires role <role>; you have <sorted roles or 'none'>")`. Table (spec §5.2): reads (`list_tasks`, `get_task`, `list_open_steps`, `get_step_context`) need any role; `create_task` → `requester`; `claim_step`/`heartbeat`/`complete_step`/non-forced `release_step` → `step.role`; `decide_gate` → `step.role`; `override_review` → `approver`; forced `release_step` → `factory_owner`. `Caller.roles` has no wildcard anymore.
  - `ServiceBase._run` calls the check inside the body so refusals are recorded as `rejected` events.
  - Ownership: `_load_owned` also requires `run.claimed_by == caller.subject` (`Rejected("step is claimed by <subject>, not you")`). `step_runs.claimed_by` now stores the subject; `claimed_executor` (new column) stores the executor; waiting-for shows `agent-b (claude-code)`.
  - `list_open_steps` returns only steps whose `role` is in `caller.roles`.
  - Schema version 2: `events` columns `subject`, `roles` (JSON list), `executor` replace `caller`; `step_runs` gains `claimed_executor`. `Store` on a v1 file raises `SchemaMismatch("<path> has schema version 1, expected 2; delete <path> to start fresh (M1 data is not migrated)")`.
  - `models.Event` fields `subject: str`, `roles: list[str]`, `executor: str | None` replace `caller`; the CLI history column shows `subject (executor)` and the roles.

- [ ] **Step 1: Write the failing tests**: one allowed and one refused call per operation in the table (refusal text exact, e.g. `"create_task requires role requester; you have planner"`); the refusal appears in `get_task` history with `result == "rejected"` and the caller's subject and roles; `agent-b` cannot heartbeat or complete a step `agent-a` claimed, even with the claim token; `list_open_steps` for a reviewer-only caller excludes planner steps; Review Focus 5: a v1 database file → `SchemaMismatch` with the delete hint.
- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest -q`
Expected: the new tests fail.

- [ ] **Step 3: Implement.** Existing service tests replace `ME = Caller("local", frozenset({"*"}), …)` with a caller holding every role.
- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/taktloop tests
git commit -m "Authorize every operation by role; audit identity, roles and executor"
```

---

### Task 6: Review steps, escalation, multi-target rejects, per-step feedback

**Files:**
- Modify: `src/taktloop/workflow.py`, `src/taktloop/config.py`, `src/taktloop/ops_steps.py`, `src/taktloop/ops_gates.py`, `src/taktloop/service.py`, `src/taktloop/rules.py`, `src/taktloop/models.py`, `src/taktloop/mcp_server.py`, `src/taktloop/cli.py`, `factory.yaml`, `templates/mvp/factory.yaml`
- Test: `tests/test_workflow.py`, `tests/test_service_review.py` (new), `tests/test_service_gates.py`, `tests/test_cli.py`

**Interfaces:**
- Produces:
  - `Step` gains `review_of: str | None`, `on_pass: str | None`, `on_issues: str | None`, `max_issue_rounds: int | None`, `on_escalate: str | None`; `on_reject` becomes `tuple[str, ...]`; property `is_review: bool`. `targets` includes all of them. Loader errors (exact): `"step 'x': review steps need review_of, on_pass and on_issues"`, `"step 'x': max_issue_rounds and on_escalate go together"`, `"step 'x': review_of must name an agent work step"`; a review step must not have `output` or `next`.
  - `FactoryConfig.separation: Literal["off", "strict"]` (default `"off"`; other values → error).
  - `complete_step(caller, step_id, claim_token, outcome, artifacts, findings: str | None = None)`: work steps accept only `completed`; review steps accept `pass`/`issues`, no artifacts, `findings` required (non-blank) for `issues`, stored as `evidence = {"findings": …}`.
  - Escalation: on `issues`, count this review step's `issues` verdicts in the task **since the last closed run of its `on_escalate` gate** (or since the start); if the count reaches `max_issue_rounds`, open `on_escalate`, else open `on_issues`.
  - `decide_gate(…, target: str | None = None)`: on reject, `target` must be one of `step.on_reject` (default: the first); otherwise `Rejected("gate <g> can send back to <a>, <b>; not <t>")`. CLI `reject <task> --reason TEXT [--to STEP]`.
  - `StepContext.rejection_reason` is replaced by `feedback: Feedback | None` with `Feedback(kind: Literal["gate_rejection", "review_issues"], text: str, by: str, at: datetime)`: the gate rejection or review findings that opened **this** run (looked up via the event that opened it), `None` in round 1. For a review step, `artifacts` includes the latest completed round of `review_of` and of every earlier step.
  - `rules.waiting_for` adds: review step → `"waiting for a reviewer to pick up plan_review"`; escalation gate → `"waiting for approver decision at plan_escalation (plan failed review 2 times)"`.
  - `factory.yaml` and the template get `separation: off`, `plan.next: plan_review`, and the `plan_review` / `plan_escalation` steps verbatim from spec §6.1.

- [ ] **Step 1: Write the failing tests**: loader accepts the spec §6.1 workflow and rejects each malformed variant with the exact message; `pass` → task done; `issues` without findings → rejected; `issues` once → plan round 2 whose `feedback.kind == "review_issues"` and `text` is the findings; second `issues` → task at `plan_escalation` with the waiting-for text above; Review Focus 2: reject from the escalation gate to `plan`, then `issues` → plan (not escalation), then `issues` → escalation again; `reject --to design` from the escalation gate → design round n+1 with `feedback.kind == "gate_rejection"`; `--to plan_review` → rejected with the message above; a design round reopened by `design_approval` still shows the gate's reason (M1 behaviour, now via `feedback`).
- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_workflow.py tests/test_service_review.py tests/test_service_gates.py -q`
Expected: the new tests fail.

- [ ] **Step 3: Implement.** `mcp_server.complete_step` gains `findings: str | None = None`; `decide_gate` gains `target: str | None = None`.
- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/taktloop tests factory.yaml templates/mvp/factory.yaml
git commit -m "Add review steps with escalation, multi-target rejects and per-step feedback"
```

---

### Task 7: Override and human review from the CLI

**Files:**
- Modify: `src/taktloop/ops_gates.py`, `src/taktloop/mcp_server.py`, `src/taktloop/cli.py`
- Test: `tests/test_service_review.py`, `tests/test_cli.py`, `tests/test_mcp_server.py` (tool list)

**Interfaces:**
- Consumes: review-step fields from Task 6; `permissions.require(…, "override_review")` from Task 5.
- Produces:
  - `override_review(caller, task_id: int, review_step: str, reason: str) -> TaskSummary`. Allowed when the task's current run is: the review step (open or claimed: claim cancelled), its `on_escalate` gate, or any run of its `review_of` step opened after the first verdict of this review step. Otherwise `Rejected("task <id> has no review of <review_of> to override")`. Closes the current run as `superseded`, opens `on_pass` (or finishes the task). Event `override_review`, detail `{"reason", "review_step", "verdicts": [{"outcome", "by", "round"}…], "approved": [artifacts of the latest completed round of review_of]}`. Earlier verdict runs are not modified.
  - MCP tool `override_review(task_id, reason, review_step="plan_review")`.
  - CLI `taktloop override <task> --reason TEXT [--step plan_review]`; `taktloop review <task> (--pass | --issues TEXT)`: reads the task, requires its current step to be a review step, then `claim_step` + `complete_step` (`outcome` `pass` / `issues`, `findings`) as the CLI's identity.

- [ ] **Step 1: Write the failing tests**: override at the review step (open, and claimed by `agent-b`: the claim is gone and the task is done); at the escalation gate; Review Focus 3: during plan round 3 (open, after two verdicts and a reject from the gate), the override's `approved` artifacts are round 2's (the latest completed), and round 3 is `superseded`; override before any review (task at plan round 1) → rejected; a planner-only caller → `"override_review requires role approver; you have planner"`; both earlier verdict runs keep outcome `issues`. CLI: `review 3 --issues "x"` against an in-memory/monkeypatched tool layer calls `claim_step` then `complete_step` with `outcome="issues", findings="x"`.
- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_service_review.py tests/test_cli.py tests/test_mcp_server.py -q`
Expected: the new tests fail.

- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/taktloop tests
git commit -m "Add approver override of plan reviews and taktloop review"
```

---

### Task 8: Strict separation of duties

**Files:**
- Create: `src/taktloop/separation.py`
- Modify: `src/taktloop/ops_base.py` (`ServiceBase.__init__` takes `separation`), `src/taktloop/ops_steps.py`, `src/taktloop/ops_gates.py`, `src/taktloop/serve.py`
- Test: `tests/test_separation.py`

**Interfaces:**
- Consumes: `Step.review_of`, `is_review` (Task 6); completed step runs with `claimed_by` = subject (Task 5).
- Produces:
  - `separation.Record(step: str, round: int, by: str)` (one per completed agent run of a task).
  - `separation.approved_step(wf: Workflow, gate: Step | None, review: Step | None) -> str`: for an override or an escalation gate, the review step's `review_of`; for any other gate, the nearest work step before it in the workflow (`design_approval` → `design`).
  - `separation.check_claim(wf, step: Step, subject: str, records: list[Record]) -> str | None` (rules 1 and 2) and `separation.check_approval(approved: str, subject: str, records) -> str | None` (rule 3), returning the refusal text or `None`. Texts: `"strict separation: agent-a completed plan round 1 of task 3"` (the earliest matching record).
  - `Service(store, workflow, lease=…, clock=…, separation: str = "off")`; with `"strict"`, `claim_step`, `decide_gate` and `override_review` raise `Rejected(<text>)`. `serve.run` passes `cfg.separation`.

- [ ] **Step 1: Write the failing tests**: strict: `agent-a` planned round 1 → its review claim refused with the exact text, `agent-b` allowed; `agent-b` reviewed → its claim of plan round 2 refused; an identity that completed `design` deciding `design_approval` → refused; an identity that completed any plan round overriding → refused; `approved_step` for `design_approval` is `design` and for `plan_escalation` is `plan`. Off: the same sequence by one identity (plan, review own plan, revise, review again) all succeed.
- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_separation.py -q`
Expected: FAIL (`ModuleNotFoundError: taktloop.separation`)

- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/taktloop tests
git commit -m "Add strict separation of duties (off by default)"
```

---

### Task 9: Skills, harness wiring, docs, boundary and budget checks, M2 end-to-end

**Files:**
- Create: `templates/mvp/skills/taktloop-plan-review/SKILL.md`, `.mcp.json`, `tests/test_boundaries.py`, `tests/test_e2e_m2.py`
- Modify: `templates/mvp/skills/taktloop/SKILL.md`, `src/taktloop/cli.py` (`install-skill` copies both skills: default dest `.claude/skills/`), `README.md`, `tests/test_templates.py`

**Interfaces:**
- Consumes: everything above.

- [ ] **Step 1: Write the failing tests**:
  - `test_boundaries.py`: parse every `src/taktloop/**/*.py` with `ast`; no module outside `issuer/` imports `taktloop.issuer` except `cli.py` (which only starts it); no module in `issuer/` imports any other `taktloop` module. Plus: total non-test lines in `src/` ≤ 2,200 and every file ≤ 300.
  - `test_templates.py`: the plan-review skill exists and mentions `pass`, `issues` and `findings`; the bridge skill mentions `TAKTLOOP_TOKEN`, `feedback`, and contains no `rejection_reason`; `install-skill --dest D` creates `D/taktloop/SKILL.md` and `D/taktloop-plan-review/SKILL.md`.
  - `test_e2e_m2.py` (marked `e2e`): real issuer and core subprocesses, `separation: strict`, the spec §9 steps 2–8 driven through MCP clients with tokens for `tobias`, `agent-a` and `agent-b`, ending with `get_task` history containing the refused claim, two `issues` verdicts, the escalation and the `override_review` event, each with subject and roles.
- [ ] **Step 2: Run them to see them fail**

Run: `uv run pytest tests/test_boundaries.py tests/test_templates.py tests/test_e2e_m2.py -q`
Expected: the new tests fail.

- [ ] **Step 3: Write the skills and wiring.** The plan-review skill follows spec §6.2 (builder check on the plan, then writing-plans' self-review checklist run independently; ranked findings; `pass` when nothing important is left; never edit the plan). The bridge skill follows spec §8 (setup with `.mcp.json` and `TAKTLOOP_TOKEN=$(uv run taktloop token <identity>) claude`; 401 → restart with a fresh token; review steps complete with `outcome` + `findings`; revision rounds read `feedback`; a role or separation refusal → report and stop, never switch identity). `.mcp.json` is spec §4.4 verbatim. README: M2 setup (`issuer init`, start issuer and core, start sessions per identity), the Codex (`bearer_token_env_var = "TAKTLOOP_TOKEN"`) and Pi (`headers` with `${TAKTLOOP_TOKEN}`) snippets, the M1-database note, and the accepted credentials limit from spec §4.2.
- [ ] **Step 4: Run the full suite**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add templates .mcp.json src/taktloop/cli.py README.md tests
git commit -m "Ship the plan-review skill, harness wiring and M2 end-to-end test"
```

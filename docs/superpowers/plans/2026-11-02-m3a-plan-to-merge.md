# M3a: plan to merge, with a hard CI gate — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** After plan review passes, a task continues through workspace → build → code review → verify → integration approval → integrate → done, and `verify` only passes when the core itself confirms, through GitHub's MCP server, that CI is green on the reviewed commit.

**Architecture:** A step may declare one `check` (an MCP tool call plus `match`/`expect` field equalities) in `factory.yaml`. Completing such a step runs the check outside any transaction through an injected `checker`, records a `check` event, then applies the result: refused on `match` mismatch, `on_fail` on `expect` mismatch, `next` on pass. The checker in production is the `mcp` SDK's streamable HTTP client with the core's own token from the environment or `.env`.

**Tech Stack:** Python ≥ 3.12, uv, `mcp` ≥ 2.3 (server and client), `pyyaml`, `pyjwt[crypto]`, SQLite, pytest.

**Spec:** `docs/superpowers/specs/2026-11-02-m3a-plan-to-merge-design.md` (approved). Earlier designs (M1, M2) still hold where it is silent.

## Global Constraints

- Direct runtime dependencies stay `mcp` (≥ 2.3), `pyyaml`, `pyjwt[crypto]`; nothing new.
- Line budget: `wc -l src/taktloop/*.py src/taktloop/issuer/*.py` total **≤ 2,600** (hard cap for M3a + M3b); M3a target **≤ ~2,380** (today 2,150, so ~+230). No module over ~300 lines. Each task below names its allowance; report the running total in each task's commit message body.
- uv only (`uv run pytest`, `uv run taktloop …`). English docs; product name "taktloop".
- No network call while a SQLite transaction is open.
- The GitHub token is never written to `factory.yaml`, the database, logs or events.
- Check language: equality only, dotted field paths, placeholders exactly `{{evidence.<key>}}`, `{{artifacts.<name>.commit|branch|path}}`. Nothing else.
- Resolved check values are strings; comparison converts both sides to strings.
- Check call timeout: 30 s.
- GitHub MCP facts (confirmed live, spec §10): URL `https://api.githubcopilot.com/mcp/x/actions/readonly`; tool `actions_get`, args `method: get_workflow_run`, `owner`, `repo`, `resource_id` (string); result `{"method": "get_workflow_run", "workflow_run": {"head_sha", "path", "status", "conclusion", …}}` as `structuredContent` and identical JSON text; unknown run → tool error.

## Review Focus

1. **The claim changes during the network call** (lease expired and someone else claimed, or a force-release): phase 2 must refuse, not apply a result to someone else's claim. Pinned by `test_check_claim_lost_during_call` (Task 4).
2. **The verifier names a run of an older build round** (the commit before review issues were fixed): must be refused against the *latest* `change` round. Pinned by `test_check_uses_latest_change_round` (Task 4).
3. **GitHub returns a tool error, garbage, or times out**: refused with "CI check could not run: …", nothing changes, the verifier can retry. Pinned by `test_check_unavailable_changes_nothing` (Task 4) and `test_external_tool_error_and_unreachable` (Task 3).
4. **`.env` with quotes, comments, blank lines or `export ` prefix**: the token is read correctly and never logged. Pinned by `test_read_dotenv_formats` (Task 3).
5. **A red run, then a review `issues`, then another review `issues`**: must not escalate, because the review count restarts after a pass. Pinned by `test_issue_count_restarts_after_pass` (Task 5).

---

### Task 1: Check declarations and workflow fields

Allowance: ~+75 lines (new `checks.py` parse part ~40, `workflow.py` ~35).

**Files:**
- Create: `src/taktloop/checks.py`
- Modify: `src/taktloop/workflow.py`
- Test: `tests/test_checks.py` (new), `tests/test_workflow.py`

**Interfaces:**
- Produces:
  - `checks.CheckDecl` frozen dataclass: `mcp_server: str`, `tool: str`, `args: Mapping[str, str]`, `match: Mapping[str, str]`, `expect: Mapping[str, str]`.
  - `checks.parse_check(raw: Any) -> CheckDecl` — raises `ValueError` (message without step prefix). Requires `mcp_server`, `tool` (strings), `expect` (non-empty mapping); `args`, `match` optional mappings; all scalar values are converted with `str()`.
  - `checks.placeholders(decl: CheckDecl) -> set[str]` — every `{{…}}` name in args, match and expect values, stripped (e.g. `"evidence.ci_run"`, `"artifacts.change.commit"`).
  - `workflow.Step` gains `evidence: tuple[str, ...] = ()`, `check: CheckDecl | None = None`, `on_fail: str | None = None`; `on_fail` is part of `Step.targets`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_checks.py`:
```python
def test_parse_check_stringifies_values():
    d = parse_check({"mcp_server": "github", "tool": "actions_get",
                     "args": {"resource_id": "{{evidence.ci_run}}", "page": 1},
                     "match": {"workflow_run.status": "completed"},
                     "expect": {"workflow_run.conclusion": "success"}})
    assert d.args == {"resource_id": "{{evidence.ci_run}}", "page": "1"}
    assert placeholders(d) == {"evidence.ci_run"}

@pytest.mark.parametrize("raw, msg", [
    ({"tool": "t", "expect": {"a": "b"}}, "mcp_server"),
    ({"mcp_server": "g", "expect": {"a": "b"}}, "tool"),
    ({"mcp_server": "g", "tool": "t"}, "expect"),
    ({"mcp_server": "g", "tool": "t", "expect": {"a": "b"}, "when": 1}, "unknown check field"),
])
def test_parse_check_refuses(raw, msg): ...  # pytest.raises(ValueError, match=msg)
```
In `tests/test_workflow.py`, using a helper that builds a minimal workflow `work` (artifact `change`) → `verify` (the spec §4 verify step shape, with `next: done`, `on_fail: work`):
- `test_check_step_loads`: `step.evidence == ("ci_run",)`, `step.check.tool == "actions_get"`, `"work" in step.targets`.
- `test_check_step_refusals`, parametrized, each raising `WorkflowError` with the message fragment:
  - check without `on_fail` → `"check, evidence and on_fail go together"`
  - `evidence` without check → same message
  - `{{evidence.run}}` not in `evidence` → `"unknown placeholder evidence.run"`
  - `{{artifacts.nope.commit}}` (no step outputs `nope`) → `"unknown placeholder artifacts.nope.commit"`
  - `{{artifacts.change.sha}}` → `"unknown placeholder artifacts.change.sha"`
  - `{{task.id}}` → `"unknown placeholder task.id"`
  - check on a review step or a human gate → `"checks are allowed on agent work steps only"`
  - `on_fail` to an unknown step → existing `"points to unknown step"`
- `test_check_step_needs_no_artifacts`: the verify step has no `output` and loads.

- [ ] **Step 2: Run to verify they fail**

Run: `uv run pytest tests/test_checks.py tests/test_workflow.py -q`
Expected: FAIL (`ModuleNotFoundError: taktloop.checks`, missing `Step.check`).

- [ ] **Step 3: Implement `parse_check`, `placeholders`, `CheckDecl` in `checks.py`, and the workflow fields**

`checks.py` must not import `workflow.py` (workflow imports checks). In `_parse_step`, wrap `ValueError` from `parse_check` as `WorkflowError(f"step {name!r}: …")`. A work step with a check needs `next`, `evidence`, `on_fail` and no `output.artifacts` requirement. Placeholder validation runs in `parse_workflow` after all steps are parsed (artifact names = union of every step's `required_artifacts`). Placeholder regex: `\{\{\s*([^{}]+?)\s*\}\}`.

- [ ] **Step 4: Run to verify they pass**

Run: `uv run pytest -q`
Expected: all pass.

- [ ] **Step 5: Commit**

```bash
git add src/taktloop/checks.py src/taktloop/workflow.py tests/test_checks.py tests/test_workflow.py
git commit -m "Declare gate checks, evidence and on_fail on workflow steps"
```

### Task 2: Check evaluation (pure)

Allowance: ~+50 lines in `checks.py`.

**Files:**
- Modify: `src/taktloop/checks.py`
- Test: `tests/test_checks.py`

**Interfaces:**
- Consumes: `CheckDecl` (Task 1); `models.Artifact` (existing).
- Produces:
  - `checks.CheckUnavailable(Exception)` — `str(e)` is the reason.
  - `checks.Mismatch` frozen dataclass: `field: str`, `got: str | None`, `want: str`.
  - `checks.resolve(decl: CheckDecl, evidence: Mapping[str, str], artifacts: Mapping[str, Artifact]) -> CheckDecl` — same decl with every placeholder in args/match/expect substituted (artifacts keyed by name, latest round).
  - `checks.read_result(structured: dict | None, texts: list[str]) -> dict` — `structured` if it is a dict, else the first text parsed as a JSON object; otherwise `CheckUnavailable("result is not a JSON object")`.
  - `checks.compare(fields: Mapping[str, str], result: Mapping) -> list[Mismatch]` — dotted-path lookup; a missing path gives `got=None`.
  - `checks.describe(m: Mismatch) -> str` — exactly `"<field> is <got>, expected <want>"`, or `"<field> is missing, expected <want>"`.

- [ ] **Step 1: Write the failing tests**

```python
RUN = {"method": "get_workflow_run", "workflow_run": {"head_sha": "9c1e" + "0" * 36,
       "path": ".github/workflows/ci.yml", "status": "in_progress", "conclusion": None, "id": 12345}}

def test_resolve_substitutes_evidence_and_artifacts(): ...
    # args {"resource_id": "{{evidence.ci_run}}"} + evidence {"ci_run": "12345"} -> "12345";
    # match {"workflow_run.head_sha": "{{artifacts.change.commit}}"} -> the artifact's commit
def test_compare_reports_each_mismatch():
    ms = compare({"workflow_run.status": "completed", "workflow_run.path": ".github/workflows/ci.yml"}, RUN)
    assert [describe(m) for m in ms] == ["workflow_run.status is in_progress, expected completed"]
def test_compare_missing_field():
    assert describe(compare({"workflow_run.nope": "x"}, RUN)[0]) == "workflow_run.nope is missing, expected x"
def test_compare_stringifies_numbers():
    assert compare({"workflow_run.id": "12345"}, RUN) == []
def test_read_result_prefers_structured_then_text_then_refuses():
    assert read_result({"a": 1}, ["garbage"]) == {"a": 1}
    assert read_result(None, ['{"a": 1}']) == {"a": 1}
    for bad in ([], ["not json"], ["[1, 2]"]):
        with pytest.raises(CheckUnavailable): read_result(None, bad)
```

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/test_checks.py -q` → FAIL (names not defined).

- [ ] **Step 3: Implement the five functions in `checks.py`.**

- [ ] **Step 4: Run to verify they pass** — `uv run pytest tests/test_checks.py -q` → PASS.

- [ ] **Step 5: Commit** — `git commit -m "Resolve and compare gate check results"`

### Task 3: External MCP servers: config, credential, client

Allowance: ~+65 lines (new `external.py` ~50, `config.py` ~15).

**Files:**
- Create: `src/taktloop/external.py`
- Modify: `src/taktloop/config.py`
- Test: `tests/test_external.py` (new), `tests/test_config.py`

**Interfaces:**
- Consumes: `checks.read_result`, `checks.CheckUnavailable` (Task 2).
- Produces:
  - `external.ExternalServer` frozen dataclass: `name: str`, `url: str`, `token_env: str`.
  - `external.read_dotenv(path: Path) -> dict[str, str]` — missing file → `{}`; `KEY=VALUE` lines; skips blanks and `#` comments; strips an `export ` prefix and one pair of matching surrounding quotes.
  - `external.credential(server: ExternalServer, environ: Mapping[str, str], dotenv: Mapping[str, str]) -> str | None` — environment first, then `.env`; empty counts as missing.
  - `external.Checker = Callable[[str, str, dict[str, str]], dict]` — `(server name, tool, args) -> result dict`, raises `CheckUnavailable`.
  - `external.make_checker(servers: Mapping[str, ExternalServer], environ: Mapping[str, str], dotenv: Mapping[str, str], timeout: float = 30.0) -> Checker`. It runs `anyio.run` (service operations run in a worker thread, so this is safe) with `anyio.fail_after(timeout)`, an `mcp.Client` over `streamable_http_client` with `create_mcp_http_client(headers={"Authorization": f"Bearer {token}"})`. Errors map to `CheckUnavailable`:
    - no token → `"the core has no credential for <name> (<TOKEN_ENV> is not set)"`
    - tool result `is_error` → the tool's first text
    - timeout → `"<name> did not answer within 30 s"`
    - any other exception → `"<name> is unreachable: <ExcType>"` (no message text, which could echo headers)
  - `config.FactoryConfig` gains `mcp_servers: Mapping[str, ExternalServer] = {}` (use `field(default_factory=dict)`) and `env_file: Path` (= `path.parent / ".env"`). `load_config` parses `mcp_servers: {name: {url, token_env}}` (both required) and refuses a step whose `check.mcp_server` is not configured: `"step 'verify': check uses unknown mcp_server 'github'"`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_external.py`:
- `test_read_dotenv_formats`: a file with `# c`, a blank line, `A=1`, `export B="two"`, `C='3'`, `D=x=y` → `{"A": "1", "B": "two", "C": "3", "D": "x=y"}`; a missing path → `{}`.
- `test_credential_env_wins_then_dotenv_then_none`.
- `test_external_checker_calls_tool`: a fake GitHub MCP server (an `MCPServer` with one tool `actions_get(method: str, owner: str, repo: str, resource_id: str) -> dict` that records its bearer token and args and returns `{"method": method, "workflow_run": {"id": int(resource_id), "status": "completed"}}`), served with uvicorn in a thread on a free port like `conftest.real_issuer`. Assert the result dict, the received `Authorization` header and `resource_id == "12345"`. Put this server in `conftest.py` as fixture `fake_github` yielding `(url, calls)`, where `calls` is a list of received args; it is reused in Tasks 4 and 7. The tool raises `ValueError("failed to get workflow run: 404 Not Found")` for `resource_id == "999"`.
- `test_external_tool_error_and_unreachable`: `"999"` → `CheckUnavailable` whose text contains `404 Not Found`; a closed port → `"github is unreachable"`; no token → `"the core has no credential for github (TAKTLOOP_GITHUB_TOKEN is not set)"`.
- `test_external_timeout`: `make_checker(..., timeout=0.2)` against a tool that sleeps 1 s → `"github did not answer within"`.

In `tests/test_config.py`: `test_mcp_servers_load` and `test_check_with_unknown_server_is_refused`.

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/test_external.py tests/test_config.py -q` → FAIL.

- [ ] **Step 3: Implement `external.py` and the config fields.**

- [ ] **Step 4: Run to verify they pass** — `uv run pytest -q` → all pass.

- [ ] **Step 5: Commit** — `git commit -m "Call external MCP servers with the core's own credential"`

### Task 4: Completing a step with evidence and a check

Allowance: ~+55 lines (`ops_steps.py` ~40, `service.py`/`ops_base.py` ~15). If `ops_steps.py` would pass ~200 lines, move the check path into `ops_checks.py`.

**Files:**
- Modify: `src/taktloop/ops_steps.py`, `src/taktloop/ops_base.py`, `src/taktloop/service.py` (`_feedback`), `src/taktloop/models.py` (`Feedback.kind`)
- Test: `tests/test_service_checks.py` (new)

**Interfaces:**
- Consumes: `Step.check/evidence/on_fail` (Task 1); `resolve`, `compare`, `describe`, `CheckUnavailable` (Task 2); `external.Checker` (Task 3).
- Produces:
  - `ServiceBase.__init__(…, checker: Checker | None = None)`; a check with no checker → `Rejected("CI check could not run: no checker configured")`.
  - `Service.complete_step(caller, step_id, claim_token, outcome, artifacts, findings=None, evidence: dict[str, str] | None = None) -> TaskSummary`.
  - Run outcome `failed` (status `completed`); `Feedback.kind` gains `"check_failed"`.
  - Core event `operation="check"`, `subject="core"`, detail `{"server", "tool", "args", "observed": {field: value-or-None for every match/expect field}, "result": "pass" | "fail" | "mismatch", "summary": str}`.

Behaviour (spec §5.3, §6.3):
- Evidence: a step with `evidence` keys requires exactly those keys, each a non-empty string → else `Rejected("evidence must be exactly: ci_run")`. A step without `evidence` refuses any evidence → `Rejected("this step takes no evidence")`. Steps without a check behave as today.
- Check path, in this order:
  1. **Validate:** a `self._run(..., writes=False)` body validates the claim, owner, token, outcome `completed` and the evidence. It resolves the check against the evidence and `latest_rounds` artifacts by name, and returns the resolved decl. Refusals are recorded, as they are today.
  2. **Call:** `checker(decl.mcp_server, decl.tool, dict(decl.args))` runs outside any transaction. On `CheckUnavailable(e)`, record a refused `complete_step` with reason `f"CI check could not run: {e}"` and raise `Rejected`. Do the recording with a `_run` whose body raises.
  3. **Record the check:** compare `match`, then `expect`. Write the `check` event in its own transaction. `summary` is `f"{decl.mcp_server}.{decl.tool} ({', '.join(f'{k}={v}' for k, v in evidence.items())}): {'; '.join(describe(m) for m in mismatches) or 'pass'}"`.
  4. **Apply:** a normal `_run` body reloads the run with `_load_owned`, so a lost claim is refused there. Then:
     - `match` mismatches → `Rejected("; ".join(describe(m) …))`.
     - `expect` mismatches → close with outcome `failed`, evidence stored, `_advance(on_fail)`.
     - Otherwise → close `completed`, `_advance(next)`.

  The `complete_step` event detail carries `evidence` and `next`.
- `_feedback`: when the previous run's outcome is `failed`, return `Feedback(kind="check_failed", text=<summary of that run's last check event with result "fail">, by="core", at=<that event's at>)`.

- [ ] **Step 1: Write the failing tests** in `tests/test_service_checks.py`. The workflow is a small fixture: `build` (artifact `change`, `next: verify`) → `verify` (spec §4 check, `next: done`, `on_fail: build`). The checker is a fake: `lambda server, tool, args: results.pop(0)`, which can also raise. Tests:
  - `test_check_pass_advances`: the result matches → `status == "done"`; the history has a `check` event by `core` with `result == "pass"` and `observed["workflow_run.conclusion"] == "success"`; the fake received `resource_id == "12345"`.
  - `test_check_match_mismatch_refuses_and_keeps_claim`: status `in_progress` → `Rejected` with `"workflow_run.status is in_progress, expected completed"`. The run is still claimed by the verifier, and a second call with a completed result passes.
  - `test_check_expect_failure_reopens_build_with_feedback`: conclusion `failure` → `current_step == "build"`, round 2. `get_step_context(...).feedback` has `kind == "check_failed"`, `by == "core"`, and the text contains `"ci_run=12345"` and `"workflow_run.conclusion is failure, expected success"`.
  - `test_check_uses_latest_change_round`: build round 1 at SHA_A, red check back to build, round 2 at SHA_B. A result with `head_sha == SHA_A` is refused (`"head_sha is " + SHA_A`), and one with SHA_B passes.
  - `test_check_unavailable_changes_nothing`: the fake raises `CheckUnavailable("github is unreachable: ConnectError")` → `Rejected("CI check could not run: github is unreachable: ConnectError")`. The run is still claimed, there is no `check` event, and there is one refused `complete_step` event.
  - `test_check_claim_lost_during_call`: the fake advances the clock past the lease and force-releases the step through the service before returning. → `Rejected` with "not claimed", and the step is not advanced.
  - `test_evidence_rules`: a missing key, an extra key or an empty value is refused on `verify`. Evidence on `build` is refused (`"this step takes no evidence"`).

- [ ] **Step 2: Run to verify they fail** — `uv run pytest tests/test_service_checks.py -q` → FAIL.

- [ ] **Step 3: Implement as specified above.**

- [ ] **Step 4: Run to verify they pass** — `uv run pytest -q` → all pass.

- [ ] **Step 5: Commit** — `git commit -m "Run gate checks on completion: refuse, fail back or advance"`

### Task 5: Review cycles and approval of artifact-less steps

Allowance: ~+5 lines.

**Files:**
- Modify: `src/taktloop/ops_steps.py` (`_after_issues`), `src/taktloop/separation.py` (`approved_step`)
- Test: `tests/test_service_review.py`, `tests/test_separation.py`

**Interfaces:** no new names. `_after_issues` counts `issues` verdicts of the review step after the later of: the review step's last `pass` run, and the last time the task left the escalation gate. `approved_step` for a plain gate returns the nearest earlier agent step that is not a review and has `required_artifacts`.

- [ ] **Step 1: Write the failing tests.**
  - `test_issue_count_restarts_after_pass`: a workflow where plan_review passes, a later gate rejects back to `plan`, and plan_review then returns `issues` once. The task must be back at `plan`, not at `plan_escalation`.
  - `test_integration_gate_approves_build`: a workflow `build`(artifact) → `verify`(check) → `gate`. Under strict separation, the identity that completed `build` is refused at `gate` with `"strict separation: A completed build round 1"`.
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement both changes.**
- [ ] **Step 4: Run `uv run pytest -q`** → all pass.
- [ ] **Step 5: Commit** — `git commit -m "Restart review rounds after a pass; approve the step that produced artifacts"`

### Task 6: Wire it up: MCP tool, serve, CLI rendering

Allowance: ~+25 lines.

**Files:**
- Modify: `src/taktloop/mcp_server.py` (`complete_step` gains `evidence: dict[str, str] | None = None`; docstring: "Steps with a check: outcome 'completed' with evidence (for example {"ci_run": "<run id>"}); the core checks the result itself"), `src/taktloop/serve.py`, `src/taktloop/cli_render.py`
- Test: `tests/test_mcp_server.py`, `tests/test_cli.py`

**Interfaces:**
- Consumes: `make_checker`, `read_dotenv`, `credential` (Task 3); `Service(checker=…)` (Task 4).
- `serve.run` builds the checker from `cfg.mcp_servers`, `os.environ` and `read_dotenv(cfg.env_file)`. For each configured server without a credential it logs one WARNING on `taktloop` at start: `"no credential for github: set TAKTLOOP_GITHUB_TOKEN in the environment or in .env; checks against it will be refused"`.
- Rendering in `_event_detail`:
  - a `check` event → `f"{detail['result']}: {detail['summary']}"`
  - a `complete_step` with `outcome == "failed"` → `f"failed → {next}"`
  - a `complete_step` carrying evidence → `"ci_run=12345"` style, joined after the artifacts.

- [ ] **Step 1: Write the failing tests**
  - In `test_mcp_server.py`: through the in-memory client, `complete_step` with `evidence` reaches the service.
  - In `test_cli.py`: rendering a history with a `check` event and a failed verify completion gives exactly `"fail: github.actions_get (ci_run=12345): workflow_run.conclusion is failure, expected success"` and `"failed → build"`.
- [ ] **Step 2: Run to verify they fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run `uv run pytest -q`** → all pass.
- [ ] **Step 5: Commit** — `git commit -m "Expose evidence on complete_step, wire the checker into serve, render checks"`

### Task 7: The M3a workflow, identities, skill, docs and end-to-end test

Allowance: ~0 `src/` lines (config, docs and tests only).

**Files:**
- Modify: `factory.yaml`, `templates/mvp/factory.yaml` (identical workflow; spec §4 verbatim, plus the `mcp_servers` block; `plan_review.on_pass` and `plan_escalation.on_approve` become `workspace`)
- Modify: `dev-issuer.yaml` (spec §6.1: both agents get planner, builder, reviewer and verifier)
- Modify: `templates/mvp/skills/taktloop/SKILL.md` (spec §8), `README.md` (M3a section)
- Modify: tests that walked to `done` after `plan_review` through the shipped workflow (`test_service_review.py`, `test_separation.py`, `test_e2e_m2.py`). They now assert `current_step == "workspace"` where they asserted `status == "done"`. Their meaning is unchanged: the plan was accepted.
- Modify: `tests/test_boundaries.py` (budget assertion 2,200 → 2,600)
- Create: `tests/test_e2e_m3a.py`

- [ ] **Step 1: Update the configs and the existing tests; run `uv run pytest -q`.** Expected: all pass, except tests already failing before this step (none).
- [ ] **Step 2: Write `tests/test_e2e_m3a.py`.** Like `test_e2e_m2.py` (real issuer and core subprocesses, `separation: strict`, the shipped workflow), but `mcp_servers.github.url` points at the `fake_github` fixture (Task 3) and `TAKTLOOP_GITHUB_TOKEN=fake` is set in the core's environment. Walk spec §9:
  - steps 1 to 4: review `issues` once, then `pass`;
  - step 5: an `in_progress` refusal, then an older-sha refusal, then pass;
  - step 6: a red run back to build with `check_failed` feedback, then review and verify again;
  - step 7: approve, integrate, `done`.

  Assert the final `task show` history contains `check` events with `pass` and `fail`, and that the `merge` artifact exists.
- [ ] **Step 3: Add a live test** to `tests/test_external.py`, `test_live_github_run`, skipped unless `TAKTLOOP_GITHUB_TOKEN` is set in the environment or the repo's `.env`. Through `make_checker` against the real URL, `get_workflow_run` for run `41508346956` must have `workflow_run.head_sha == "b4c593058b64762308b72f0225f63ccb12b07b98"`, `workflow_run.path == ".github/workflows/ci.yml"` and `conclusion == "success"`.
- [ ] **Step 4: Bridge skill and README.**
  - The skill follows spec §8:
    - artifact `path` `"."` for commit-only artifacts;
    - push before completing build;
    - review `workspace.commit..change.commit`;
    - verify: run `gh run list --commit <sha>`, wait, then `complete_step` with `evidence={"ci_run": "<id>"}`. A `status` refusal means wait and retry; a `head_sha` refusal means wrong run; never state a result;
    - integrate: merge the change commit, push `main`, never force-push.
  - Extend `test_templates.py` to require the words `evidence` and `ci_run` in the skill.
  - The README covers:
    - creating the fine-grained token (only `medlor/taktloop`; Actions: read; Metadata: read);
    - putting it in `.env` as `TAKTLOOP_GITHUB_TOKEN=…`;
    - the M3a demo;
    - the known limits from spec §4 (CI config lives on the branch) and §6.6 (merge not verified).
- [ ] **Step 5: Run the full suite and the budget.**
  - `uv run pytest -q` → all pass (the live test runs here when `.env` has the token).
  - `wc -l src/taktloop/*.py src/taktloop/issuer/*.py | tail -1` → ≤ 2,380 target; must be ≤ 2,600. Report the number.
- [ ] **Step 6: Commit and push.**
```bash
git add factory.yaml templates dev-issuer.yaml README.md tests
git commit -m "Ship the M3a workflow: build, code review, CI-checked verify, integrate"
git push origin main
```
  Expected: the GitHub Actions run for this commit is green (`gh run list --commit $(git rev-parse HEAD)`). The e2e tests must pass on the runner. If the runner can't start the subprocess servers, fix that; don't skip the tests.

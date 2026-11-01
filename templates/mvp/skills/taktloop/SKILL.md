---
name: taktloop
description: Use when the user says "work on taktloop" or asks you to pick up, claim or continue a taktloop step or task. Runs one workflow step through the taktloop core's MCP tools, then stops at the step boundary.
---

# taktloop bridge

taktloop is a task core you reach through MCP tools. A task moves through steps
(for example design, then a human approval gate, then plan). You run **exactly
one step** per session, hand back its artifacts, and stop.

If the taktloop tools (`list_open_steps`, `claim_step`, ...) are not available,
tell the user to register them and stop:

    claude mcp add --transport http taktloop http://127.0.0.1:8765/mcp

(the core must be running: `uv run taktloop serve`). Registering takes effect
only in a new session, so tell the user to restart Claude Code after running
it. Do nothing else.

## Procedure

1. **Find work.** Call `list_open_steps`. Offer the steps to the user and let
   them choose; if the session is headless, pick the first one. If there are
   none, say so and stop.
2. **Claim.** Call `claim_step(step_id)`. **Keep the returned `claim_token`**:
   `heartbeat`, `complete_step` and `release_step` need it. A rejection such as
   "already claimed" means another session holds the step: report it, stop.
3. **Get the context.** Call `get_step_context(step_id)`. It gives the task
   title and request, the `skill` to use, the step `instructions`, the
   `required_artifacts`, the `round`, a `rejection_reason` (or null) and the
   `artifacts` produced by earlier steps.
4. **Run the step with the skill the context names**, following the step
   `instructions` and the task request.
   - Read an earlier artifact from git, not the working tree:
     `git show <commit>:<path>`.
   - If `rejection_reason` is set, this is a revision round: the previous
     result was rejected. Read the earlier artifact, address the reason, and
     produce a revised version.
5. **Finish the skill's own review first.** An artifact is final only after the
   running skill's in-chat review checkpoint is done (for example "please
   review the design doc") and the user is happy with it. Make the requested
   changes, then continue. Never complete at the first draft or first commit.
   Order: run skill -> in-chat review until the user is happy -> commit the
   final version -> `complete_step` with that commit -> stop.

   **Step boundary beats skill chaining.** Once the artifact is final, do
   **not** invoke the skill the running skill says comes next (for example a
   brainstorming skill that says to invoke writing-plans, or a planning skill
   that asks for an execution mode). Those belong to later steps, claimed by
   later sessions.
6. **Commit the final version, then complete.**
   - Commit each final artifact on the current branch (after the review).
   - Call `complete_step(step_id, claim_token, artifacts=[...])` with one entry
     per required artifact: `name` (as listed in `required_artifacts`), `path`
     (repo-relative), `commit` (the full 40-character SHA from
     `git rev-parse HEAD`) and `branch` (from `git branch --show-current`).
   - A rejected completion (bad path, short SHA, missing artifact, wrong
     token) changes nothing. Read the reason, fix it, and call again. If the
     reason says the step is not claimed, see "Long sessions and failure".
7. **Stop.** After `complete_step` succeeds, say what the task is now waiting
   for (the result names the next step and role), then end your work. Do not
   claim another step.

## Approval gates

Skills often ask the user to approve a result in chat (for example "approve
the design doc"). Hold that conversation as the skill asks, but the **binding**
approval is the core's gate, not the chat. After completing, tell the user:

    uv run taktloop approve <task_id>
    uv run taktloop reject <task_id> --reason "..."

They can do this at any time, from anywhere. Do not call `decide_gate`
yourself.

## Long sessions and failure

- Leases last 30 minutes. Call `heartbeat(step_id, claim_token)` once when a
  new user message arrives, never more than once per turn. Do not add an
  extra heartbeat before `complete_step`; if the lease ran out, the call says
  so and you recover as below.
- If `heartbeat` or `complete_step` says the step is not claimed, the lease
  expired or a human released it. While you are still working the step with
  the user, call `claim_step(step_id)` again and use the **new**
  `claim_token`. If that is rejected (someone else holds it, or it is no
  longer open), report it and stop. If the user says it was released on
  purpose, do not re-claim.
- If the user abandons the work, call `release_step(step_id, reason,
  claim_token)` with a short reason, so another session can take over. If it
  answers "step is not claimed (already free)", there is nothing left to do.
- If you fail or get stuck, call `release_step` with the reason. Never fake a
  completion or invent a commit SHA.

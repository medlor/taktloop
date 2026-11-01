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

(the core must be running: `uv run taktloop serve`). Do nothing else.

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
5. **Step boundary beats skill chaining.** When every required artifact exists,
   you are done with this step. Do **not** invoke the skill the running skill
   says comes next (for example a brainstorming skill that says to invoke
   writing-plans, or a planning skill that asks for an execution mode). Those
   belong to later steps, claimed by later sessions.
6. **Commit, then complete.**
   - Commit each artifact on the current branch.
   - Call `complete_step(step_id, claim_token, artifacts=[...])` with one entry
     per required artifact: `name` (as listed in `required_artifacts`), `path`
     (repo-relative), `commit` (the full 40-character SHA from
     `git rev-parse HEAD`) and `branch` (from `git branch --show-current`).
   - A rejected completion (bad path, short SHA, missing artifact, wrong
     token) changes nothing. Read the reason, fix it, and call again.
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

- Leases last 30 minutes. In a long conversation call
  `heartbeat(step_id, claim_token)` about every 15 minutes.
- If the user abandons the work, call `release_step(step_id, reason,
  claim_token)` with a short reason, so another session can take over.
- If you fail or get stuck, call `release_step` with the reason. Never fake a
  completion or invent a commit SHA.

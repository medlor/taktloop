---
name: taktloop-breakout
description: Use when the user asks to change the taktloop workflow of a running factory (add, change or remove a step, change the start, a route or who does a step). Edits a draft through the core's workflow tools and hands it to the factory owner for approval.
---

# taktloop breakout: change the workflow while the factory runs

The workflow lives in the factory's `workflow.yaml`, but **only the core writes
that file**. You change it through the core's MCP tools, against a **draft**.
The factory owner approves the draft; then it becomes the next version. Tasks
already running finish on the version they started with; new tasks take the
new one.

You run as an identity with the `factory_assistant` role (for example
`assistant`: `TAKTLOOP_TOKEN=$(taktloop token assistant --config <factory
dir>/factory.yaml) claude`). You can edit the draft; you **cannot approve it**.

The step format (every key, routes, placeholders, code steps, review steps,
gates) is in the factory's docs: `docs/user/workflow-reference.md` in the
taktloop repository. Read it before writing a step.

## Tools

| Tool | Does |
| --- | --- |
| `get_workflow()` | the live workflow as `data` (`start`, `steps`) and its `version` |
| `get_workflow(draft=true)` | the draft, its `base_version` and its `problems` |
| `put_step(name, definition)` | add or replace one step in the draft; a replaced step keeps its place |
| `remove_step(name)` | remove a step from the draft |
| `set_start(name)` | change the draft's start step |
| `discard_draft()` | drop the draft |
| `approve_workflow(base_version)` | the factory owner only; not you |

The first edit starts the draft as a copy of the live version. Each editing
call returns the draft with its `problems`.

## Refused vs. problems

- **Refused**: the call fails with a reason and the draft is **unchanged**.
  That is anything that can't be right even mid-edit: a malformed step, an
  unknown kind or key, a missing required field (`role`, `skill`, an agent
  step's `output.artifacts` or `next`), bad `routes` or `push`, an
  `mcp_server` the factory doesn't have, a reserved name (`done`,
  `cancelled`). Read the reason, fix the definition, call again.
- **Problems**: the call succeeds, and `problems` lists the gaps that are
  normal while editing: a target that doesn't exist yet, a step nothing leads
  to, an unknown placeholder, a missing start step. Keep editing until the
  list is empty. A draft with problems cannot be approved.

## Procedure

1. `get_workflow()`: read the live steps. Talk the change through with the
   user: which step goes where, who does it (`role`), which steps lead to it
   and where it leads.
2. Make the change one step at a time. Adding a step between two others takes
   two calls: `put_step` the new step (its `next` is the old successor), then
   `put_step` the predecessor again with its `next` pointing at the new step.
   Send the **whole** definition each time; `put_step` replaces, it does not
   merge.
3. Give **every step a `description`**: one line saying what the step is for.
   It shows in `task show` and in the step context. No comments: the core
   rewrites the file, so YAML comments do not survive.
4. When `problems` is empty, show the user the draft
   (`get_workflow(draft=true)`) and what changed.
5. **Ask the owner to approve.** Tell the user the owner's commands:

       taktloop workflow draft                 # show the draft and its problems
       taktloop workflow approve               # make it the next version
       taktloop workflow discard               # or drop it

   Approval is refused if the draft has problems, if someone approved another
   version in between (look again and redo the edit on the new version), or
   if a tool the draft names is not available on its server.

## Rules

- Never edit `workflow.yaml` (or `factory.yaml`) by hand, never call
  `approve_workflow`, never get a token for another identity.
- One change at a time; do not tidy up steps the user did not ask about.
- Removing `dispatch: push` from a step (put it again without it) moves the
  work from a headless bot to anyone who pulls the step; nothing else changes.

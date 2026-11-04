---
name: taktloop-plan-review
description: Use when a taktloop step context names the taktloop-plan-review skill, to review an implementation plan against its approved design doc (or the task's request when there is none) and return a pass or issues verdict with ranked findings.
---

# taktloop plan review

You review an implementation plan that another agent wrote. You are the
reviewer, not the planner: you **never edit the plan** (or the design doc).
Your result is a verdict, `pass` or `issues`, plus findings. The `taktloop`
bridge skill claims the step and completes it; this skill is what you do in
between.

## Inputs

The step context (`get_step_context`) gives you:

- `review_of`: the step whose work you review (here `plan`).
- `artifacts`: the pinned artifacts of earlier steps. Use the **latest round**
  of the `plan` artifact and the approved `design_doc`.
- `request`: the task's request. A task that triage routed **small** has
  **no design doc**: then the request is the requirement, and wherever this
  skill says "design doc" below, read "the task's request".

Read both from git, never from the working tree:

    git show <commit>:<path>

The plan may build on code that already exists; read the repository at the
plan's commit when a task refers to it.

## Review

Work through both checks yourself, independently of the plan's own
self-review.

1. **Builder check on the plan.** You must implement this plan without
   talking to its author. Go task by task: where would you have to guess, and
   where would a wrong guess matter? Missing file paths, interfaces, values,
   commands or expected results count; so do steps that contradict each other
   or the design doc.
2. **The writing-plans self-review checklist, run independently:**
   - **Design coverage:** every requirement in the design doc has a task that
     implements it. List any requirement without one.
   - **No placeholders:** no "TBD", "add appropriate handling", "similar to
     Task N", or steps that describe code without showing it.
   - **Consistent names:** types, functions, files and commands are named the
     same way in every task that uses them.
   - **Tests for the risky cases:** the error paths, edge cases and decisions
     the design calls out are tested, not only the happy path.

Do not report style preferences or alternatives the design already ruled out.

## Verdict

Write the findings as a **ranked list**, most important first. Each finding
names the task (or design section), what is wrong or missing, and why a
builder would get it wrong.

- **pass** when nothing important is left: a builder could implement the plan
  without guessing anywhere a wrong guess matters. Minor findings may still be
  listed.
- **issues** otherwise. The findings go back to the planner as the next
  round's `feedback`, so make each one concrete and actionable.

Show the verdict and findings to the user in chat. Then hand back to the
bridge skill, which calls:

    complete_step(step_id, claim_token, outcome="pass" | "issues", findings="...")

with no artifacts. `findings` is required for `issues`.

## Rules

- Never edit, commit or rewrite the plan or the design doc, even for a
  one-line fix: put it in the findings.
- If the core refuses your claim for strict separation, report the reason and
  stop.
- Review only. Do not start implementing the plan.

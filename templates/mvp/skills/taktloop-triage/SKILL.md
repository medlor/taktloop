---
name: taktloop-triage
description: Use when a taktloop step context names the taktloop-triage skill, to sort a new task into one route (quick, small, project or park) with a one-line reason, without asking anything.
---

# taktloop triage

You sort a new task before anyone works on it. You are a headless run: the
`taktloop` bridge skill claims the step and completes it; this skill is what
you decide in between. **Never ask the user anything**, never edit files,
never start the work itself.

## Inputs

The step context (`get_step_context`) gives the task `task_title` and
`request` and the step `instructions`, which name the routes. (The step's
`routes` themselves are in `get_workflow`, under `steps.triage.routes`.) The repository is the product checkout
you were started in; read it with Read, Grep, Glob and `git log`.

## Three questions

Like the first check of brainstorming, answer these for yourself:

1. **What kind of change is this?** A typo, a config value, a one-line fix,
   a new command, a new subsystem, a change of behaviour users will notice?
2. **Does the request say what is wanted?** Could a builder act on it without
   asking the requester? Is the expected result clear?
3. **How much of the repository does it touch?** Find the files. One spot,
   one module, or several parts with a design decision between them?

## Routes

- **quick**: obvious and tiny, one or two files, no design decision and no
  plan needed (a typo, a renamed flag, a missing test for existing code). Goes
  straight to `workspace` and `build`; code review, CI and the integration
  approval still hold.
- **small**: clear and contained, but worth a written plan (a new option, a
  bug fix across a few files). Goes to `plan`, written from the request.
- **project**: needs a design conversation with the requester first (new
  behaviour, several modules, open choices). Goes to `design`.
- **park**: the request is not ready, contradicts itself, or you cannot tell.
  A human then decides at `triage_decision`. **When in doubt, park.** Parking
  is not a failure; guessing a route is.

## Result

Hand back to the bridge skill, which calls

    complete_step(step_id, claim_token, outcome="<route>", findings="<reason>")

with no artifacts: `outcome` is exactly one route name (quick, small, project or park), `findings` one line saying why (for example "one-line typo fix in
README.md" or "request names no expected behaviour for empty input"). An
unknown route is refused; read the reason and call again with a listed one.

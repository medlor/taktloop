taktloop

## M1: try it

Start the core, register it with Claude Code, and move one task through
design, approval and plan across separate sessions.

```sh
uv sync                                  # install dependencies
uv run taktloop serve                    # start the core (keep this terminal open)
```

In another terminal:

```sh
claude mcp add --transport http taktloop http://127.0.0.1:8765/mcp   # register the core's MCP tools
uv run taktloop install-skill            # copy the bridge skill to .claude/skills/taktloop/
uv run taktloop task create "Add a --version flag to the CLI"        # task 1, waiting for a planner
```

In Claude Code (session A), say **work on taktloop**. It claims the design
step, writes and commits the design doc, completes the step and stops. Close
the session, then:

```sh
uv run taktloop task show 1              # waiting for the approver, with the full history
uv run taktloop approve 1                # approve the design; the plan step opens
uv run taktloop reject 1 --reason "..."  # or reject it: design reopens as round 2 with your reason
```

In a new Claude Code session (B), say **work on taktloop** again. It claims
the plan step, reads the approved design doc, writes the plan and stops.
`uv run taktloop task show 1` then shows the task as done with both artifacts.

Restarting the core in between is safe: state lives in `.taktloop/factory.db`.

`uv run pytest` runs the tests, including an end-to-end test that restarts the
core while a step is claimed.

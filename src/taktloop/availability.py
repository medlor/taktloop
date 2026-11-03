"""Are the external MCP servers the workflow needs reachable, and do they have the tools?"""

import time
from collections.abc import Callable, Collection, Mapping

from taktloop.checks import CheckUnavailable
from taktloop.config import SyncConfig
from taktloop.workflow import Workflow


def needed_tools(wf: Workflow, kinds: Collection[str], sync: Mapping[str, SyncConfig] = {},
                 extra: Mapping[str, set[str]] = {}) -> dict[str, set[str]]:
    """server -> tools: the `kinds` ("check", "call") declarations of the steps, sync checks, `extra`."""
    out = {server: set(tools) for server, tools in extra.items()}
    decls = [getattr(s, k) for s in wf.steps.values() for k in kinds] + [c.check for c in sync.values()]
    for d in filter(None, decls):
        out.setdefault(d.mcp_server, set()).add(d.tool)
    return out


def describe(problems: Mapping[str, str]) -> str:
    return "; ".join(f"{server} {problem}" for server, problem in problems.items())


class Availability:
    def __init__(self, lister: Callable[[str], set[str]], needed: Callable[[], dict[str, set[str]]],
                 clock: Callable[[], float] = time.monotonic, every: float = 60.0) -> None:
        self._lister, self._needed, self._clock, self._every = lister, needed, clock, every
        self._cached: tuple[float, dict[str, str]] | None = None

    def _probe(self, needs: dict[str, set[str]]) -> dict[str, str]:
        out = {}
        for server, tools in sorted(needs.items()):
            try:
                if missing := sorted(tools - self._lister(server)):
                    out[server] = f"missing tool {', '.join(missing)}"
            except CheckUnavailable as e:
                out[server] = str(e).removeprefix(f"{server} is ")
        return out

    def problems(self) -> dict[str, str]:
        """server -> problem, re-probed at most once per `every` seconds."""
        if self._cached is None or self._clock() - self._cached[0] >= self._every:
            self._cached = (self._clock(), self._probe(self._needed()))
        return self._cached[1]

    def check(self, needs: dict[str, set[str]]) -> str | None:
        """Uncached: the problems of `needs` as one text, or None."""
        return describe(self._probe(needs)) or None

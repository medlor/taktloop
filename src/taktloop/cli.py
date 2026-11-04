"""The ``taktloop`` command line: argument parsing and dispatch (handlers live in cli_commands)."""

import argparse
from importlib.metadata import version
from pathlib import Path

import yaml

from taktloop.cli_commands import _fail, _issuer, _orchestrator, _serve, _token, run_client
from taktloop.cli_setup import _connect, _init, _up
from taktloop.cli_tickets import _tickets
from taktloop.config import load_config


def _parser() -> argparse.ArgumentParser:
    def common(parser: argparse.ArgumentParser, **defaults) -> None:
        parser.add_argument("--config", type=Path, **defaults.get("config", {}))
        parser.add_argument("--url", help="core URL (overrides the config; no config is read)",
                            **defaults.get("url", {}))
        parser.add_argument("--json", action="store_true", help="print structured results as JSON",
                            **defaults.get("json", {}))

    p = argparse.ArgumentParser(prog="taktloop")
    p.add_argument("--version", action="version", version=f"%(prog)s {version('taktloop')}")
    common(p, config={"default": Path("factory.yaml")})
    # subparser copies must not overwrite a value given before the subcommand
    hide = {"default": argparse.SUPPRESS}
    shared = argparse.ArgumentParser(add_help=False)
    common(shared, config=hide, url=hide, json=hide)
    sub = p.add_subparsers(dest="cmd", required=True)
    ident = argparse.ArgumentParser(add_help=False)
    ident.add_argument("--as", dest="identity", metavar="IDENTITY", help="fetch a token for this identity")
    client = [shared, ident]
    sub.add_parser("serve", parents=[shared], help="run the core")
    sub.add_parser("orchestrator", parents=[shared], help="run the orchestrator (code steps, pushes, stalls)")
    task = sub.add_parser("task", parents=client, help="create, list and show tasks").add_subparsers(dest="sub", required=True)
    c = task.add_parser("create", parents=client)
    c.add_argument("title")
    c.add_argument("--request", default="", help="request text, or - to read stdin")
    c.add_argument("--route", help="take this route of the routed step at once (approver only)")
    task.add_parser("list", parents=client).add_argument("--all", action="store_true", help="include closed tasks")
    task.add_parser("show", parents=client).add_argument("id", type=int)
    a = sub.add_parser("approve", parents=client)
    a.add_argument("task", type=int)
    a.add_argument("--gate")
    r = sub.add_parser("reject", parents=client)
    r.add_argument("task", type=int)
    r.add_argument("--reason", required=True)
    r.add_argument("--gate")
    r.add_argument("--to", help="step to send the task back to (default: the gate's first)")
    rt = sub.add_parser("route", parents=client, help="route a task at a gate with routes")
    rt.add_argument("task", type=int)
    rt.add_argument("name")
    rt.add_argument("--reason", required=True)
    rv = sub.add_parser("review", parents=client, help="pass a review verdict (claims and completes)")
    rv.add_argument("task", type=int)
    verdict = rv.add_mutually_exclusive_group(required=True)
    verdict.add_argument("--pass", dest="passed", action="store_true")
    verdict.add_argument("--issues", metavar="TEXT")
    o = sub.add_parser("override", parents=client, help="approve a reviewed step regardless of verdicts")
    o.add_argument("task", type=int)
    o.add_argument("--reason", required=True)
    o.add_argument("--step", default="plan_review", help="the review step (default: plan_review)")
    rel = sub.add_parser("release", parents=client, help="force-release a claimed step")
    rel.add_argument("step_id", type=int)
    rel.add_argument("--force", action="store_true", required=True)
    rel.add_argument("--reason", required=True)
    sub.add_parser("status", parents=client, help="show the factory, workflow version and server availability")
    sub.add_parser("steps", parents=client, help="list open steps")
    wf = sub.add_parser("workflow", parents=client, help="show and change the workflow").add_subparsers(dest="sub", required=True)
    wf.add_parser("show", parents=client).add_argument("--version", type=int)
    for name in ("draft", "discard", "approve"):
        wf.add_parser(name, parents=client)
    for name in ("remove", "start"):
        wf.add_parser(name, parents=client).add_argument("name")
    put = wf.add_parser("put", parents=client)
    put.add_argument("name")
    put.add_argument("file", type=Path)
    iss = sub.add_parser("issuer", help="run the dev issuer (development only)")
    iss.add_argument("--config", type=Path, default=Path("dev-issuer.yaml"))
    iss_init = iss.add_subparsers(dest="sub").add_parser("init", help="create the signing key and credentials")
    iss_init.add_argument("--config", type=Path, default=argparse.SUPPRESS)
    iss_init.add_argument("--rotate", metavar="ID", help="issue a new secret for one identity")
    iss_init.add_argument("--credentials", type=Path, help="default: .taktloop/credentials next to the config")
    t = sub.add_parser("token", help="print a dev-issuer token for an identity")
    t.add_argument("identity")
    t.add_argument("--config", type=Path, default=argparse.SUPPRESS, help="factory.yaml; locates the credentials")
    t.add_argument("--credentials", type=Path, help="default: .taktloop/credentials next to the config")
    tk = sub.add_parser("tickets", parents=[shared], help="the ticket stand-in: serve it or manage tickets")
    tk = tk.add_subparsers(dest="sub", required=True)
    ts = tk.add_parser("serve", help="run the stand-in")
    ts.add_argument("--port", type=int, default=8767)
    ts.add_argument("--database", type=Path, default=Path(".taktloop/tickets.db"))
    tk.add_parser("list", parents=[shared]).add_argument("--all", action="store_true", help="include closed tickets")
    for name in ("show", "close", "reopen", "delete"):
        cmd = tk.add_parser(name, parents=[shared])
        cmd.add_argument("id", type=int)
        if name == "close":
            cmd.add_argument("--resolution", default="")
    i = sub.add_parser("init", help="create a factory directory from the template")
    i.add_argument("dir", type=Path)
    i.add_argument("--repo", required=True, metavar="OWNER/REPO", help="the product repo on GitHub")
    i.add_argument("--checkout", type=Path, required=True, help="the product repo's local checkout")
    sub.add_parser("up", parents=[shared], help="run issuer, tickets, core and orchestrator until Ctrl-C")
    cn = sub.add_parser("connect", help="install the skills and .mcp.json entries into a product repo")
    cn.add_argument("core_url", metavar="CORE_URL")
    cn.add_argument("--tickets-url")
    cn.add_argument("--dest", type=Path, default=Path("."))
    return p


def main(argv: list[str] | None = None) -> int:
    ns = _parser().parse_args(argv)
    local = {"init": _init, "up": _up, "connect": _connect, "issuer": _issuer, "token": _token, "tickets": _tickets}
    if ns.cmd in local:  # these read no factory.yaml up front
        return local[ns.cmd](ns)
    try:
        cfg = None if (ns.url and ns.cmd not in ("serve", "orchestrator")) else load_config(ns.config)
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as e:
        return _fail(f"taktloop: {e}")
    if ns.cmd == "serve":
        return _serve(cfg)
    if ns.cmd == "orchestrator":
        return _orchestrator(ns, cfg)
    return run_client(ns, cfg)


if __name__ == "__main__":
    raise SystemExit(main())

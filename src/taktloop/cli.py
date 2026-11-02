"""The ``taktloop`` command line: argument parsing and dispatch (handlers live in cli_commands)."""

import argparse
import sys
from importlib.metadata import version
from pathlib import Path

import yaml

from taktloop.cli_commands import _install_skill, _issuer, _serve, _token, run_client
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
    task = sub.add_parser("task", parents=client, help="create, list and show tasks").add_subparsers(dest="sub", required=True)
    c = task.add_parser("create", parents=client)
    c.add_argument("title")
    c.add_argument("--request", default="", help="request text, or - to read stdin")
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
    sub.add_parser("steps", parents=client, help="list open steps")
    iss = sub.add_parser("issuer", help="run the dev issuer (development only)")
    iss.add_argument("--config", type=Path, default=Path("dev-issuer.yaml"))
    iss_init = iss.add_subparsers(dest="sub").add_parser("init", help="create the signing key and credentials")
    iss_init.add_argument("--config", type=Path, default=argparse.SUPPRESS)
    iss_init.add_argument("--rotate", metavar="ID", help="issue a new secret for one identity")
    iss_init.add_argument("--credentials", type=Path, default=Path(".taktloop/credentials"))
    t = sub.add_parser("token", help="print a dev-issuer token for an identity")
    t.add_argument("identity")
    t.add_argument("--credentials", type=Path, default=Path(".taktloop/credentials"))
    s = sub.add_parser("install-skill", parents=[shared], help="copy the bridge skill into a project")
    s.add_argument("--dest", type=Path, default=Path(".claude/skills/taktloop"))
    return p


def main(argv: list[str] | None = None) -> int:
    ns = _parser().parse_args(argv)
    if ns.cmd == "install-skill":
        return _install_skill(ns.dest)
    if ns.cmd == "issuer":
        return _issuer(ns)
    if ns.cmd == "token":
        return _token(ns)
    try:
        cfg = None if (ns.url and ns.cmd != "serve") else load_config(ns.config)
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as e:
        print(f"taktloop: {e}", file=sys.stderr)
        return 2
    if ns.cmd == "serve":
        return _serve(cfg)
    return run_client(ns, cfg)

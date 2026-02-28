"""Command-line interface for contextweaver.

Provides seven sub-commands:

demo        Run a built-in demonstration of both engines.
build       Compile a context pack from a JSONL session file.
route       Route a query over a tool catalog.
print-tree  Pretty-print the routing tree for a catalog.
init        Scaffold a new contextweaver project directory.
ingest      Ingest a raw tool result into the artifact store.
replay      Replay a recorded session from a JSONL file.
"""

from __future__ import annotations

import argparse
import sys


def _cmd_demo(args: argparse.Namespace) -> int:  # noqa: ARG001
    """Run a built-in demonstration of both engines."""
    print("contextweaver demo — coming soon.")
    return 0


def _cmd_build(args: argparse.Namespace) -> int:
    """Compile a context pack from a JSONL session file."""
    print(f"build: session={args.session!r}  phase={args.phase!r}")
    return 0


def _cmd_route(args: argparse.Namespace) -> int:
    """Route a query over a tool catalog."""
    print(f"route: query={args.query!r}  catalog={args.catalog!r}")
    return 0


def _cmd_print_tree(args: argparse.Namespace) -> int:
    """Pretty-print the routing tree for a catalog."""
    print(f"print-tree: catalog={args.catalog!r}")
    return 0


def _cmd_init(args: argparse.Namespace) -> int:
    """Scaffold a new contextweaver project directory."""
    print(f"init: target={args.target!r}")
    return 0


def _cmd_ingest(args: argparse.Namespace) -> int:
    """Ingest a raw tool result into the artifact store."""
    print(f"ingest: source={args.source!r}")
    return 0


def _cmd_replay(args: argparse.Namespace) -> int:
    """Replay a recorded session from a JSONL file."""
    print(f"replay: session={args.session!r}")
    return 0


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="contextweaver",
        description="Dynamic context management for tool-using AI agents.",
    )
    sub = parser.add_subparsers(dest="command", metavar="COMMAND")

    # demo
    sub.add_parser("demo", help="Run a built-in demonstration of both engines.")

    # build
    p_build = sub.add_parser("build", help="Compile a context pack from a session file.")
    p_build.add_argument("session", help="Path to a JSONL session file.")
    p_build.add_argument(
        "--phase",
        default="answer",
        choices=["route", "call", "interpret", "answer"],
        help="Execution phase (default: answer).",
    )

    # route
    p_route = sub.add_parser("route", help="Route a query over a tool catalog.")
    p_route.add_argument("query", help="The user query to route.")
    p_route.add_argument("catalog", help="Path to the tool catalog JSON file.")

    # print-tree
    p_tree = sub.add_parser("print-tree", help="Pretty-print the routing tree.")
    p_tree.add_argument("catalog", help="Path to the tool catalog JSON file.")

    # init
    p_init = sub.add_parser("init", help="Scaffold a new contextweaver project.")
    p_init.add_argument("target", nargs="?", default=".", help="Target directory (default: .).")

    # ingest
    p_ingest = sub.add_parser("ingest", help="Ingest a raw tool result.")
    p_ingest.add_argument("source", help="Path to the raw artifact file.")

    # replay
    p_replay = sub.add_parser("replay", help="Replay a session from a JSONL file.")
    p_replay.add_argument("session", help="Path to the JSONL session file.")

    return parser


_HANDLERS = {
    "demo": _cmd_demo,
    "build": _cmd_build,
    "route": _cmd_route,
    "print-tree": _cmd_print_tree,
    "init": _cmd_init,
    "ingest": _cmd_ingest,
    "replay": _cmd_replay,
}


def main() -> None:
    """Entry point for the ``contextweaver`` CLI."""
    parser = _build_parser()
    args = parser.parse_args()
    if args.command is None:
        parser.print_help()
        sys.exit(0)
    handler = _HANDLERS.get(args.command)
    if handler is None:
        parser.print_help()
        sys.exit(1)
    sys.exit(handler(args))


if __name__ == "__main__":
    main()

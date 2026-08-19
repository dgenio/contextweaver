"""Minimal command-line surface for the D1 survival experiment (#856).

This module intentionally stays separate from ContextWeaver's historical
runtime CLI. Run it as ``python -m contextweaver.d1`` while the product
hypothesis is being tested.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Sequence

from contextweaver.d1_snapshot import (
    _validate_snapshot,
    build_snapshot,
    diff_snapshots,
    inspect_snapshot,
    load_snapshot,
)
from contextweaver.exceptions import CatalogError


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CatalogError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CatalogError(f"invalid JSON in {path}: {exc}") from exc


def _write_json(path: Path | None, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if path is None:
        sys.stdout.write(text)
    else:
        path.write_text(text, encoding="utf-8")


def _cmd_snapshot(args: argparse.Namespace) -> int:
    _write_json(args.output, build_snapshot(args.source, args.source_type))
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    _write_json(None, inspect_snapshot(load_snapshot(args.snapshot)))
    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    report = diff_snapshots(load_snapshot(args.before), load_snapshot(args.after))
    _write_json(None, report)
    return 1 if args.check and report["has_changes"] else 0


def _cmd_verify(args: argparse.Namespace) -> int:
    raw = _read_json(args.snapshot)
    problems = _validate_snapshot(raw)
    _write_json(None, {"ok": not problems, "problems": problems})
    return 0 if not problems else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m contextweaver.d1",
        description="Offline D1 experiment: snapshot, inspect, diff and verify capability surfaces.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    snapshot = sub.add_parser("snapshot", help="Normalize a source into a deterministic snapshot.")
    snapshot.add_argument("source", type=Path)
    snapshot.add_argument(
        "--source-type",
        required=True,
        choices=("native", "mcp", "openapi"),
        help="Input format; explicit so format inference cannot silently change semantics.",
    )
    snapshot.add_argument("--output", "-o", required=True, type=Path)
    snapshot.set_defaults(func=_cmd_snapshot)

    inspect = sub.add_parser("inspect", help="Show a compact snapshot receipt.")
    inspect.add_argument("snapshot", type=Path)
    inspect.set_defaults(func=_cmd_inspect)

    diff = sub.add_parser("diff", help="Report semantic paths changed between two snapshots.")
    diff.add_argument("before", type=Path)
    diff.add_argument("after", type=Path)
    diff.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 when a capability change is present; useful as an explicit CI gate.",
    )
    diff.set_defaults(func=_cmd_diff)

    verify = sub.add_parser("verify", help="Verify snapshot structure and capability digest.")
    verify.add_argument("snapshot", type=Path)
    verify.set_defaults(func=_cmd_verify)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (CatalogError, OSError, ValueError) as exc:
        sys.stderr.write(f"contextweaver d1: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "_validate_snapshot",
    "build_snapshot",
    "diff_snapshots",
    "inspect_snapshot",
    "load_snapshot",
    "main",
]

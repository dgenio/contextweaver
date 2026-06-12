#!/usr/bin/env python3
"""Enforce the ≤300-lines-per-module convention mechanically (issue #456).

``AGENTS.md`` and ``docs/agent-context/invariants.md`` state a ≤300-line module
convention with a small named exemption list, but the rule was never mechanically
checked and the codebase had drifted to dozens of silent violators — which trains
contributors (human and agent) to ignore the doc.  This check makes the rule and
the codebase agree:

- The named exemptions (``EXEMPT``) are never checked — they match the docs.
- Pre-existing oversized modules are **grandfathered** at their current size in
  ``scripts/module_size_baseline.json`` and frozen: they may shrink freely but
  may never grow past their recorded baseline.
- Every other (non-exempt, non-grandfathered) module must stay ≤300 lines, so
  **new** violations are blocked at the gate.

Usage::

    python scripts/check_module_size.py            # gate (exit non-zero on violation)
    python scripts/check_module_size.py --update     # refresh the frozen baseline

``--update`` re-snapshots the baseline to the current oversized set; run it only
when intentionally decomposing a grandfathered module (which lowers its ceiling)
or when a deliberate, reviewed addition needs grandfathering.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SRC_ROOT = REPO_ROOT / "src" / "contextweaver"
BASELINE_PATH = REPO_ROOT / "scripts" / "module_size_baseline.json"

LIMIT = 300

# Mirrors the exemptions documented in AGENTS.md / invariants.md. Keep in sync.
EXEMPT = frozenset(
    {
        "types.py",
        "envelope.py",
        "__main__.py",
        "_mcp_cli.py",
        "_demos.py",
    }
)


def _line_count(path: Path) -> int:
    with path.open(encoding="utf-8") as handle:
        return sum(1 for _ in handle)


def _current_sizes() -> dict[str, int]:
    """Return ``{relpath: line_count}`` for every non-exempt source module."""
    sizes: dict[str, int] = {}
    for path in sorted(SRC_ROOT.rglob("*.py")):
        if path.name in EXEMPT:
            continue
        sizes[path.relative_to(SRC_ROOT).as_posix()] = _line_count(path)
    return sizes


def _load_baseline() -> dict[str, int]:
    if not BASELINE_PATH.exists():
        return {}
    data: dict[str, int] = json.loads(BASELINE_PATH.read_text(encoding="utf-8"))
    return data


def _write_baseline(sizes: dict[str, int]) -> dict[str, int]:
    frozen = {rel: loc for rel, loc in sizes.items() if loc > LIMIT}
    BASELINE_PATH.write_text(json.dumps(frozen, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return frozen


def check() -> int:
    """Gate the convention. Returns 0 when clean, 1 on any violation."""
    sizes = _current_sizes()
    baseline = _load_baseline()
    new_violations: list[str] = []
    growth: list[str] = []

    for rel, loc in sizes.items():
        if rel in baseline:
            if loc > baseline[rel]:
                growth.append(f"  {rel}: {loc} lines (grandfathered ceiling {baseline[rel]})")
        elif loc > LIMIT:
            new_violations.append(f"  {rel}: {loc} lines (limit {LIMIT})")

    stale = sorted(set(baseline) - set(sizes))

    if new_violations:
        print(
            f"module-size: {len(new_violations)} new module(s) exceed {LIMIT} lines — "
            "decompose them (mixins/helpers) before merging:",
            file=sys.stderr,
        )
        for line in sorted(new_violations):
            print(line, file=sys.stderr)
    if growth:
        print(
            f"module-size: {len(growth)} grandfathered module(s) grew past their frozen "
            "ceiling — split out the new code or decompose:",
            file=sys.stderr,
        )
        for line in sorted(growth):
            print(line, file=sys.stderr)
    if stale:
        print(
            "module-size: baseline lists modules that no longer exist; run "
            "`make module-size-update`:",
            file=sys.stderr,
        )
        for rel in stale:
            print(f"  {rel}", file=sys.stderr)

    if new_violations or growth or stale:
        return 1
    print(f"module-size: OK ({len(sizes)} modules, {len(baseline)} grandfathered ≤ frozen ceiling)")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--update",
        action="store_true",
        help="Re-snapshot the frozen baseline to the current oversized set.",
    )
    args = parser.parse_args(argv)

    if args.update:
        frozen = _write_baseline(_current_sizes())
        try:
            shown = BASELINE_PATH.relative_to(REPO_ROOT)
        except ValueError:
            shown = BASELINE_PATH
        print(f"wrote {shown} ({len(frozen)} grandfathered modules)")
        return 0
    return check()


if __name__ == "__main__":
    raise SystemExit(main())

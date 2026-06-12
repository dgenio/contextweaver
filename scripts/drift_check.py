#!/usr/bin/env python3
"""Unified generated-artifact drift gate (issue #522).

Runs every registered generator's ``--check`` in one pass (the gate behind
``make drift-check``) or regenerates them all (``make drift``).  Each generator
keeps its own standalone CLI; this harness simply composes their ``main`` entry
points so the per-artifact drift discipline has one command, one CI step, and a
single registration entry for the next generated artifact.

Usage::

    python scripts/drift_check.py            # regenerate every artifact
    python scripts/drift_check.py --check     # gate: exit non-zero on any drift

The shared compare/write/report logic lives in :mod:`_golden`; this module owns
only the *registry* and the uniform aggregate summary.
"""

from __future__ import annotations

import argparse
import sys
from collections.abc import Callable, Sequence

# These siblings live in scripts/, which is on sys.path[0] when this file is run
# directly (``python scripts/drift_check.py``) and is inserted by the test
# harness, so the bare imports resolve in both contexts.
import context_rot_demo
import gen_api_manifest
import gen_llms
import gen_schemas
import record_demo
import render_gateway_scorecard
import render_scorecard

# Registry: (artifact name, main(argv) -> exit code). Adding the next generated
# artifact is a single line here — that is the whole point of #522.
_GENERATORS: list[tuple[str, Callable[[Sequence[str] | None], int]]] = [
    ("schemas", gen_schemas.main),
    ("scorecard", render_scorecard.main),
    ("gateway-scorecard", render_gateway_scorecard.main),
    ("recorded-demos", record_demo.main),
    ("llms", gen_llms.main),
    ("context-rot", context_rot_demo.main),
    ("api-manifest", gen_api_manifest.main),
]


def main(argv: Sequence[str] | None = None) -> int:
    """Regenerate or check every registered generated artifact."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if any registered artifact has drifted (does not write).",
    )
    args = parser.parse_args(argv)
    sub_argv = ["--check"] if args.check else []

    failures: list[str] = []
    for name, generator in _GENERATORS:
        if generator(sub_argv) != 0:
            failures.append(name)

    verb = "check" if args.check else "regeneration"
    if failures:
        print(
            f"\ndrift {verb} FAILED for {len(failures)}/{len(_GENERATORS)} artifact(s): "
            f"{', '.join(failures)}",
            file=sys.stderr,
        )
        return 1
    if args.check:
        print(f"\nall {len(_GENERATORS)} generated artifacts up to date")
    else:
        print(f"\nregenerated all {len(_GENERATORS)} generated artifacts")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

#!/usr/bin/env python3
"""Fail before release when version-specific release artifacts are incomplete.

The v0.18.0 GitHub Release was published while the required deterministic
benchmark snapshot was missing. The release-triggered publish workflow caught
that inconsistency, correctly refused to upload to PyPI, and left the canonical
package channel two releases behind GitHub.

This guard moves the same invariant earlier: normal PR/main CI can prove that
the package version already has the release snapshot required by publish.yml and
that benchmarks/trend.md is rendered from the committed history.

The script is intentionally stdlib-only apart from importing sibling release
helpers under scripts/.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from pathlib import Path

from check_version_metadata import read_pyproject_version
from render_trend import load_history, render

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PYPROJECT = REPO_ROOT / "pyproject.toml"
DEFAULT_HISTORY_DIR = REPO_ROOT / "benchmarks" / "results" / "history"
DEFAULT_TREND = REPO_ROOT / "benchmarks" / "trend.md"


def find_release_readiness_problems(
    version: str,
    history_dir: Path,
    trend_path: Path,
) -> list[str]:
    """Return release-readiness problems for *version* without mutating files."""
    problems: list[str] = []
    snapshot_path = history_dir / f"{version}.json"

    if not snapshot_path.exists():
        problems.append(
            f"missing release benchmark snapshot: {snapshot_path}; capture it with "
            f"`python scripts/render_trend.py --snapshot {version} "
            "--from benchmarks/results/latest.json`"
        )
    else:
        try:
            snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            problems.append(f"invalid release benchmark snapshot {snapshot_path}: {exc}")
        else:
            if snapshot.get("release") != version:
                problems.append(
                    f"release snapshot {snapshot_path.name} declares "
                    f"{snapshot.get('release')!r}, expected {version!r}"
                )
            if snapshot.get("schema_version") != 1:
                problems.append(
                    f"release snapshot {snapshot_path.name} has unsupported "
                    f"schema_version={snapshot.get('schema_version')!r}, expected 1"
                )

    expected_trend = render(load_history(history_dir))
    if not trend_path.exists():
        problems.append(f"missing rendered benchmark trend: {trend_path}")
    else:
        actual_trend = trend_path.read_text(encoding="utf-8")
        if actual_trend != expected_trend:
            problems.append(
                f"{trend_path} is stale for the committed release history; run `make trend`"
            )

    return problems


def main(argv: Sequence[str] | None = None) -> int:
    """Check the current package version's release snapshot and trend artifact."""
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        print(f"error: unexpected arguments: {' '.join(args)}", file=sys.stderr)
        return 2

    version = read_pyproject_version(DEFAULT_PYPROJECT)
    problems = find_release_readiness_problems(version, DEFAULT_HISTORY_DIR, DEFAULT_TREND)
    if problems:
        print(f"error: release {version} is not ready:", file=sys.stderr)
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "Prepare the version-specific release artifacts before publishing a GitHub Release.",
            file=sys.stderr,
        )
        return 1

    print(f"release readiness artifacts are complete for {version}.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

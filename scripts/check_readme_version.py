#!/usr/bin/env python3
"""Fail if the README's version references drift from ``pyproject.toml`` (#347).

The README used to hard-code ``Current package version: 0.11.0`` (and a
comparison-table self-reference) by hand, which silently lagged the published
release and made an otherwise polished project read as unmaintained. This guard
makes the package version in ``pyproject.toml`` the single source of truth and
fails CI whenever a tracked README reference disagrees with it.

Three references are checked:

1. The ``Current package version: **X.Y.Z**`` line in the Roadmap section.
2. The comparison-table self-reference ``(this repo, [vX.Y.Z](...))``.
3. Roadmap rows marked ``✅ current (vX.Y.Z)``.

Usage::

    python scripts/check_readme_version.py          # exits non-zero on drift

The script is intentionally stdlib-only — no contextweaver import and no
``tomllib`` (unavailable on Python 3.10) — so it runs before the package is
installed, matching the ``scripts/render_scorecard.py`` / ``scripts/gen_llms.py``
convention.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PYPROJECT = REPO_ROOT / "pyproject.toml"
DEFAULT_README = REPO_ROOT / "README.md"

# The ``[project]`` table body: from the ``[project]`` header up to the next
# top-level table header (``[...]`` at column 0) or end of file.
_PROJECT_TABLE_RE = re.compile(r"^\[project\][^\n]*\n(.*?)(?=^\[|\Z)", re.MULTILINE | re.DOTALL)
# A ``version = "X"`` assignment (column 0) — scoped to the [project] table so a
# ``version`` in another table (build-system, a tool section) is never matched.
_VERSION_RE = re.compile(r'^version = "([^"]+)"', re.MULTILINE)
# ``Current package version: **X.Y.Z**`` (Roadmap section).
_README_CURRENT_RE = re.compile(r"Current package version: \*\*([^*]+)\*\*")
# Comparison-table self-reference: ``(this repo, [vX.Y.Z]``.
_README_COMPARE_RE = re.compile(r"this repo,\s*\[v([0-9][^\]]*)\]")
# Roadmap status row: ``| **v0.14.1 — ...** | ✅ current (v0.14.1) | ... |``.
_ROADMAP_CURRENT_RE = re.compile(
    r"^\|\s*\*\*([^|]+?)\*\*\s*\|\s*✅ current(?: \(([^)]+)\))?\s*\|",
    re.MULTILINE,
)


def read_pyproject_version(pyproject: Path) -> str:
    """Return the ``[project]`` version string from *pyproject*.

    Scans only the ``[project]`` table, so a ``version`` assignment in any other
    top-level table cannot be mistaken for the package version.
    """
    text = pyproject.read_text(encoding="utf-8")
    table = _PROJECT_TABLE_RE.search(text)
    if table is None:
        raise ValueError(f"could not find a [project] table in {pyproject}")
    match = _VERSION_RE.search(table.group(1))
    if not match:
        raise ValueError(f"could not find a version in the [project] table of {pyproject}")
    return match.group(1)


def find_drift(version: str, readme_text: str) -> list[str]:
    """Return human-readable drift messages for *readme_text* vs *version*."""
    problems: list[str] = []

    current = _README_CURRENT_RE.search(readme_text)
    if current is None:
        problems.append("README is missing the 'Current package version: **X**' line.")
    elif current.group(1) != version:
        problems.append(
            f"README 'Current package version' is {current.group(1)!r}, expected {version!r}."
        )

    compare = _README_COMPARE_RE.search(readme_text)
    if compare is None:
        problems.append("README is missing the comparison-table '(this repo, [vX])' reference.")
    elif compare.group(1) != version:
        problems.append(
            f"README comparison self-reference is 'v{compare.group(1)}', expected 'v{version}'."
        )

    expected_marker = f"v{version}"
    current_rows = _ROADMAP_CURRENT_RE.findall(readme_text)
    if not current_rows:
        problems.append("README roadmap is missing a '✅ current (vX)' marker row.")
    for milestone, marker in current_rows:
        if marker != expected_marker:
            problems.append(
                "README roadmap current marker for "
                f"{milestone!r} is {marker!r}, expected {expected_marker!r}."
            )
        if expected_marker not in milestone:
            problems.append(
                f"README roadmap current row {milestone!r} does not name {expected_marker!r}."
            )

    return problems


def main(argv: Sequence[str] | None = None) -> int:
    """Check README version references. Returns 0 when in sync, 1 on drift."""
    # No flags today; argv is accepted for symmetry with the other scripts and
    # to keep the call shape stable for tests.
    _ = argv
    version = read_pyproject_version(DEFAULT_PYPROJECT)
    problems = find_drift(version, DEFAULT_README.read_text(encoding="utf-8"))
    if problems:
        print(
            f"error: README version references are out of date (pyproject is {version}):",
            file=sys.stderr,
        )
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print("Fix the README references (or update pyproject) and re-run.", file=sys.stderr)
        return 1
    print(f"README version references are in sync with pyproject ({version}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

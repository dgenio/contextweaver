#!/usr/bin/env python3
"""Fail if release metadata references drift from their sources of truth (#347).

The README used to hard-code ``Current package version: 0.11.0`` (and a
comparison-table self-reference) by hand, which silently lagged the published
release and made an otherwise polished project read as unmaintained. This guard
makes the package version in ``pyproject.toml`` the single source of truth and
fails CI whenever a tracked README reference disagrees with it.

The same gate also keeps Python support metadata honest: the CI matrix is the
source of truth for supported Python minors, and PyPI classifiers must match it
exactly.

README references checked:

1. The ``Current package version: **X.Y.Z**`` line in the Roadmap section.
2. The comparison-table self-reference ``(this repo, [vX.Y.Z](...))``.
3. Roadmap rows marked ``✅ current (vX.Y.Z)``.

Python metadata checked:

1. The ``Programming Language :: Python :: 3.x`` classifiers against CI.

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
DEFAULT_CI = REPO_ROOT / ".github" / "workflows" / "ci.yml"

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
# Python minor classifiers from the [project] classifiers list. The generic
# ``Programming Language :: Python :: 3`` classifier is intentionally ignored.
_PYTHON_CLASSIFIER_RE = re.compile(r'"Programming Language :: Python :: (3\.\d+)"')
# CI matrix form: ``python-version: ["3.10", "3.11", ...]``.
_CI_PYTHON_MATRIX_RE = re.compile(r"python-version:\s*\[([^\]]+)\]")
_QUOTED_RE = re.compile(r'"([^"]+)"')
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


def read_pyproject_python_classifiers(pyproject: Path) -> list[str]:
    """Return Python minor classifiers from the ``[project]`` table."""
    text = pyproject.read_text(encoding="utf-8")
    table = _PROJECT_TABLE_RE.search(text)
    if table is None:
        raise ValueError(f"could not find a [project] table in {pyproject}")
    return sorted(set(_PYTHON_CLASSIFIER_RE.findall(table.group(1))), key=_version_key)


def read_ci_python_versions(ci_file: Path) -> list[str]:
    """Return Python minor versions from the CI matrix."""
    text = ci_file.read_text(encoding="utf-8")
    match = _CI_PYTHON_MATRIX_RE.search(text)
    if match is None:
        raise ValueError(f"could not find a python-version matrix in {ci_file}")
    return sorted(set(_QUOTED_RE.findall(match.group(1))), key=_version_key)


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


def find_classifier_drift(classifiers: list[str], ci_versions: list[str]) -> list[str]:
    """Return drift messages when Python classifiers and CI matrix differ."""
    problems: list[str] = []
    classifier_set = set(classifiers)
    ci_set = set(ci_versions)
    missing = sorted(ci_set - classifier_set, key=_version_key)
    extra = sorted(classifier_set - ci_set, key=_version_key)
    if missing:
        problems.append(f"pyproject classifiers are missing CI Python versions: {missing}.")
    if extra:
        problems.append(f"pyproject classifiers include versions not in CI: {extra}.")
    return problems


def _version_key(version: str) -> tuple[int, ...]:
    return tuple(int(part) for part in version.split("."))


def main(argv: Sequence[str] | None = None) -> int:
    """Check README version references and Python classifiers."""
    # No flags today; argv is accepted for symmetry with the other scripts and
    # to keep the call shape stable for tests.
    _ = argv
    version = read_pyproject_version(DEFAULT_PYPROJECT)
    problems = find_drift(version, DEFAULT_README.read_text(encoding="utf-8"))
    problems.extend(
        find_classifier_drift(
            read_pyproject_python_classifiers(DEFAULT_PYPROJECT),
            read_ci_python_versions(DEFAULT_CI),
        )
    )
    if problems:
        print(
            f"error: release metadata references are out of date (pyproject is {version}):",
            file=sys.stderr,
        )
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "Fix the stale references (or update their source of truth) and re-run.",
            file=sys.stderr,
        )
        return 1
    print(f"README version references and Python classifiers are in sync ({version}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

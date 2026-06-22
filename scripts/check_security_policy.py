#!/usr/bin/env python3
"""Fail when SECURITY.md drifts from its sources of truth (#691, umbrella #443).

``SECURITY.md`` carried a hand-maintained "Supported Versions" table that
silently lagged the released version (it claimed ``0.14.x`` while the package
had moved to ``0.16.0``). A stale security policy is worse than an absent one:
it tells reporters and adopters the wrong thing about which releases receive
fixes. This guard makes ``pyproject.toml`` the single source of truth for the
supported minor series and fails CI on drift.

It also performs the policy *link check* (#691 acceptance criteria): every
repo-relative document linked from ``SECURITY.md`` must exist on disk, so the
policy never points reporters at a moved or deleted page (e.g. the
``docs/security_model.md`` deployment-boundary guide or the
``docs/security_tooling.md`` exception runbook).

Checks:

1. The "Supported Versions" table marks the current ``MAJOR.MINOR`` series
   (derived from ``pyproject.toml``) as supported.
2. The table does not still mark a *different* minor as the supported series.
3. Every repo-relative Markdown link target in ``SECURITY.md`` resolves to an
   existing file.

Usage::

    python scripts/check_security_policy.py     # exits non-zero on drift

Stdlib-only — no contextweaver import and no ``tomllib`` (unavailable on
Python 3.10) — so it runs before the package is installed, matching the
``scripts/check_readme_version.py`` convention.
"""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PYPROJECT = REPO_ROOT / "pyproject.toml"
DEFAULT_SECURITY = REPO_ROOT / "SECURITY.md"

# The ``[project]`` table body: from the ``[project]`` header up to the next
# top-level table header (``[...]`` at column 0) or end of file.
_PROJECT_TABLE_RE = re.compile(r"^\[project\][^\n]*\n(.*?)(?=^\[|\Z)", re.MULTILINE | re.DOTALL)
_VERSION_RE = re.compile(r'^version = "([^"]+)"', re.MULTILINE)
# A supported-version table row: ``| 0.16.x | Yes |`` (the ``.x`` is optional so
# both ``0.16`` and ``0.16.x`` forms match). Captures the minor series and the
# Yes/No support flag.
_SUPPORT_ROW_RE = re.compile(
    r"^\|\s*(\d+\.\d+)(?:\.x)?\s*\|\s*(Yes|No)\s*\|",
    re.MULTILINE | re.IGNORECASE,
)
# A repo-relative Markdown link target, e.g. ``[text](docs/security_model.md)``.
# Anchors (``#frag``) and absolute URLs (``http://``, ``mailto:``) are skipped.
_MD_LINK_RE = re.compile(r"\]\(([^)]+)\)")


def read_pyproject_version(pyproject: Path) -> str:
    """Return the ``[project]`` version string from *pyproject*."""
    text = pyproject.read_text(encoding="utf-8")
    table = _PROJECT_TABLE_RE.search(text)
    if table is None:
        raise ValueError(f"could not find a [project] table in {pyproject}")
    match = _VERSION_RE.search(table.group(1))
    if not match:
        raise ValueError(f"could not find a version in the [project] table of {pyproject}")
    return match.group(1)


def current_minor(version: str) -> str:
    """Return the ``MAJOR.MINOR`` series for *version* (``0.16.0`` -> ``0.16``)."""
    parts = version.split(".")
    if len(parts) < 2:
        raise ValueError(f"version {version!r} is not MAJOR.MINOR.PATCH")
    return f"{parts[0]}.{parts[1]}"


def find_supported_drift(version: str, security_text: str) -> list[str]:
    """Return drift messages for the Supported Versions table vs *version*."""
    problems: list[str] = []
    minor = current_minor(version)
    rows = _SUPPORT_ROW_RE.findall(security_text)
    if not rows:
        problems.append("SECURITY.md has no recognisable 'Supported Versions' table rows.")
        return problems

    supported = {series for series, flag in rows if flag.lower() == "yes"}
    if minor not in supported:
        problems.append(
            f"SECURITY.md does not mark the current series '{minor}.x' as supported "
            f"(package version is {version}); supported rows are {sorted(supported)}."
        )
    # A different minor still flagged as the supported series is stale: the
    # policy supports only the latest minor (per SECURITY.md's own preamble).
    stale = sorted(s for s in supported if s != minor)
    if stale:
        problems.append(
            f"SECURITY.md still marks {stale} as supported; only the current "
            f"series '{minor}.x' should be 'Yes' under the latest-minor policy."
        )
    return problems


def find_broken_links(security_path: Path) -> list[str]:
    """Return messages for repo-relative links in *security_path* that 404."""
    problems: list[str] = []
    text = security_path.read_text(encoding="utf-8")
    repo_root = security_path.resolve().parent
    for target in _MD_LINK_RE.findall(text):
        link = target.strip()
        # Skip absolute URLs, anchors, and mail links — only repo-relative
        # filesystem paths are verifiable here.
        if link.startswith(("http://", "https://", "mailto:", "#")):
            continue
        path_part = link.split("#", 1)[0]
        if not path_part:
            continue
        resolved = (repo_root / path_part).resolve()
        if not resolved.exists():
            problems.append(f"SECURITY.md links to '{path_part}', which does not exist.")
    return problems


def main(argv: Sequence[str] | None = None) -> int:
    """Check SECURITY.md supported-version table and relative links."""
    _ = argv
    version = read_pyproject_version(DEFAULT_PYPROJECT)
    problems = find_supported_drift(version, DEFAULT_SECURITY.read_text(encoding="utf-8"))
    problems.extend(find_broken_links(DEFAULT_SECURITY))
    if problems:
        print(
            f"error: SECURITY.md is out of sync with its sources of truth "
            f"(pyproject is {version}):",
            file=sys.stderr,
        )
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "Update SECURITY.md (or the source of truth) and re-run.",
            file=sys.stderr,
        )
        return 1
    print(f"SECURITY.md supported-version table and links are in sync ({version}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

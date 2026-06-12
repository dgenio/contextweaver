#!/usr/bin/env python3
"""Shared golden-file drift-check primitives (issue #522).

The repo gates a growing family of *generated artifacts* — schemas, scorecards,
recorded demos, ``llms.txt``, the context-rot SVG, the public-API manifest, and
more.  Every generator historically re-implemented the same "render, compare to
disk, print an actionable hint, exit non-zero on drift" logic with subtly
different diff output and exit conventions.  This module is the single
implementation of that pattern; generators render a ``{path: content}`` mapping
and delegate the compare/write/report to the two functions here.

Usage from a generator::

    from _golden import check_text_artifacts, write_text_artifacts

    rendered = {OUTPUT: render(payload)}
    if args.check:
        return check_text_artifacts(rendered, label="scorecard", regen="make scorecard")
    write_text_artifacts(rendered)

``scripts/drift_check.py`` composes the per-artifact generators into one gate so
adding the next generated artifact costs a single registration entry rather than
a fresh copy of this logic.
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _rel(path: Path) -> str:
    """Render *path* relative to the repo root when possible (stable output)."""
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def drifted_paths(rendered: Mapping[Path, str]) -> list[Path]:
    """Return the subset of *rendered* paths whose on-disk content differs.

    A missing file counts as drift.  Comparison is exact (byte-for-byte after
    UTF-8 decode), matching the existing generators' contracts.
    """
    drifted: list[Path] = []
    for path, expected in rendered.items():
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current != expected:
            drifted.append(path)
    return drifted


def check_text_artifacts(
    rendered: Mapping[Path, str],
    *,
    label: str,
    regen: str,
) -> int:
    """Compare *rendered* artifacts to disk; return 0 if clean, 1 on drift.

    Prints a uniform, actionable message to stderr naming every drifted path and
    the *regen* command that fixes it.  This is the engine of every
    ``make <x>-check`` target and of :mod:`drift_check`.

    Args:
        rendered: Mapping of target path to its freshly-rendered content.
        label: Human-readable artifact family name (e.g. ``"scorecard"``).
        regen: The exact command that regenerates the artifacts (e.g.
            ``"make scorecard"``).

    Returns:
        ``0`` when every artifact matches disk, ``1`` when any has drifted.
    """
    drifted = drifted_paths(rendered)
    if drifted:
        print(f"{label}: drift detected — run `{regen}` and commit the result:", file=sys.stderr)
        for path in drifted:
            print(f"  {_rel(path)}", file=sys.stderr)
        return 1
    print(f"{label}: up to date ({len(rendered)} artifact(s))")
    return 0


def write_text_artifacts(rendered: Mapping[Path, str]) -> None:
    """Write every *rendered* artifact to disk, creating parent dirs as needed.

    Writes with an explicit ``\\n`` newline so generated artifacts stay
    byte-identical across platforms (the macOS leg of the tool-run smoke
    matrix and the recorded-cast determinism contract both depend on this).
    """
    for path, content in rendered.items():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8", newline="\n")

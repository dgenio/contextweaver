"""Tests for the README version-drift guard (#347).

The guard makes ``pyproject.toml`` the single source of truth for the version
references in the README and fails CI on drift. These unit tests pin the
detection logic (including the synthetic drift / in-sync / missing-line cases
that are awkward to reproduce against the live README) and assert the real
repository is currently in sync.

The guard lives under ``scripts/``, so it is added to ``sys.path`` the same way
:mod:`tests.test_render_scorecard` does.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import check_readme_version  # noqa: E402  (import after sys.path manipulation)


def test_read_pyproject_version_matches_semver() -> None:
    """The [project] version is read and looks like a release string."""
    version = check_readme_version.read_pyproject_version(check_readme_version.DEFAULT_PYPROJECT)
    assert re.fullmatch(r"\d+\.\d+\.\d+\S*", version)


def test_find_drift_flags_mismatch() -> None:
    """Both tracked references are flagged when they lag the version."""
    readme = "Current package version: **0.11.0**.\n... (this repo, [v0.10.0](url)) ..."
    problems = check_readme_version.find_drift("0.12.0", readme)
    assert len(problems) == 2
    assert any("Current package version" in p for p in problems)
    assert any("comparison self-reference" in p for p in problems)


def test_find_drift_passes_when_in_sync() -> None:
    """No problems when both references equal the version."""
    readme = "Current package version: **0.12.0**.\n... (this repo, [v0.12.0](url)) ..."
    assert check_readme_version.find_drift("0.12.0", readme) == []


def test_find_drift_reports_missing_references() -> None:
    """Missing references are reported rather than silently passing."""
    problems = check_readme_version.find_drift("0.12.0", "no version references here")
    assert len(problems) == 2
    assert all("missing" in p for p in problems)


def test_repo_readme_is_in_sync() -> None:
    """The production gate: the committed README matches pyproject."""
    assert check_readme_version.main() == 0

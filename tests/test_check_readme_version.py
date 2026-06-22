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

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import check_readme_version  # noqa: E402  (import after sys.path manipulation)


def test_read_pyproject_version_matches_semver() -> None:
    """The [project] version is read and looks like a release string."""
    version = check_readme_version.read_pyproject_version(check_readme_version.DEFAULT_PYPROJECT)
    assert re.fullmatch(r"\d+\.\d+\.\d+\S*", version)


def test_read_pyproject_version_is_scoped_to_project_table(tmp_path: Path) -> None:
    """A ``version`` in another table must not shadow the [project] version."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[build-system]\nrequires = ["setuptools"]\nversion = "9.9.9"\n\n'
        '[project]\nname = "x"\nversion = "1.2.3"\n\n'
        '[tool.whatever]\nversion = "0.0.1"\n',
        encoding="utf-8",
    )
    assert check_readme_version.read_pyproject_version(pyproject) == "1.2.3"


def test_read_pyproject_python_classifiers(tmp_path: Path) -> None:
    """Python minor classifiers are read from the [project] table only."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[project]\nname = "x"\nversion = "1.2.3"\n'
        "classifiers = [\n"
        '  "Programming Language :: Python :: 3",\n'
        '  "Programming Language :: Python :: 3.11",\n'
        '  "Programming Language :: Python :: 3.10",\n'
        "]\n\n"
        '[tool.fake]\nclassifiers = ["Programming Language :: Python :: 3.99"]\n',
        encoding="utf-8",
    )
    assert check_readme_version.read_pyproject_python_classifiers(pyproject) == [
        "3.10",
        "3.11",
    ]


def test_read_ci_python_versions(tmp_path: Path) -> None:
    """The CI support matrix is parsed from the quoted python-version list."""
    ci = tmp_path / "ci.yml"
    ci.write_text('python-version: ["3.12", "3.10", "3.11"]\n', encoding="utf-8")
    assert check_readme_version.read_ci_python_versions(ci) == ["3.10", "3.11", "3.12"]


def test_find_drift_flags_mismatch() -> None:
    """All tracked references are flagged when they lag the version."""
    readme = (
        "Current package version: **0.11.0**.\n"
        "... (this repo, [v0.10.0](url)) ...\n"
        "| **v0.11 — Old** | ✅ current (v0.11.0) | stale |"
    )
    problems = check_readme_version.find_drift("0.12.0", readme)
    assert len(problems) == 4
    assert any("Current package version" in p for p in problems)
    assert any("comparison self-reference" in p for p in problems)
    assert any("roadmap current marker" in p for p in problems)
    assert any("does not name" in p for p in problems)


def test_find_drift_passes_when_in_sync() -> None:
    """No problems when tracked references equal the version."""
    readme = (
        "Current package version: **0.12.0**.\n"
        "... (this repo, [v0.12.0](url)) ...\n"
        "| **v0.12.0 — Current** | ✅ current (v0.12.0) | ok |"
    )
    assert check_readme_version.find_drift("0.12.0", readme) == []


def test_find_drift_matches_dated_current_roadmap_row() -> None:
    """The current roadmap row may include a date after the bold milestone."""
    readme = (
        "Current package version: **0.12.0**.\n"
        "... (this repo, [v0.12.0](url)) ...\n"
        "| **v0.12.0 — Current** (2026-06-16) | ✅ current (v0.12.0) | ok |"
    )
    assert check_readme_version.find_drift("0.12.0", readme) == []


def test_find_drift_reports_missing_references() -> None:
    """Missing references are reported rather than silently passing."""
    problems = check_readme_version.find_drift("0.12.0", "no version references here")
    assert len(problems) == 3
    assert all("missing" in p for p in problems)


def test_find_classifier_drift_flags_missing_and_extra_versions() -> None:
    """Classifiers must match the gating CI matrix exactly."""
    problems = check_readme_version.find_classifier_drift(
        classifiers=["3.10", "3.12", "3.14"],
        ci_versions=["3.10", "3.11", "3.12"],
    )
    assert any("missing CI Python versions: ['3.11']" in p for p in problems)
    assert any("versions not in CI: ['3.14']" in p for p in problems)


def test_find_classifier_drift_passes_when_in_sync() -> None:
    """No classifier drift is reported when both surfaces match."""
    assert (
        check_readme_version.find_classifier_drift(
            classifiers=["3.10", "3.11", "3.12", "3.13"],
            ci_versions=["3.10", "3.11", "3.12", "3.13"],
        )
        == []
    )


def test_find_drift_flags_current_marker_without_explicit_version() -> None:
    """The roadmap current marker must include the pyproject version."""
    readme = (
        "Current package version: **0.12.0**.\n"
        "... (this repo, [v0.12.0](url)) ...\n"
        "| **v0.11 — Old** | ✅ current | stale |"
    )
    problems = check_readme_version.find_drift("0.12.0", readme)
    assert any("roadmap current marker" in p for p in problems)
    assert any("does not name" in p for p in problems)


def test_print_version_outputs_bare_pyproject_version(
    capsys: pytest.CaptureFixture[str],
) -> None:
    """``--print-version`` prints exactly the pyproject version and runs no checks.

    The release-integrity gate in ``publish.yml`` (#468) compares the release tag
    against this output (``[ "$tag" != "v$version" ]``), so it must be the bare
    version with no surrounding text — a trailing line or banner would silently
    break the tag-vs-version comparison.
    """
    expected = check_readme_version.read_pyproject_version(check_readme_version.DEFAULT_PYPROJECT)
    assert check_readme_version.main(["--print-version"]) == 0
    assert capsys.readouterr().out == f"{expected}\n"


def test_repo_readme_is_in_sync() -> None:
    """The production gate: the committed README matches pyproject."""
    assert check_readme_version.main() == 0

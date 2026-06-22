"""Tests for the SECURITY.md drift guard (#691).

The guard makes ``pyproject.toml`` the single source of truth for the supported
minor series advertised in ``SECURITY.md`` and verifies that every
repo-relative link the policy references still resolves. These unit tests pin
the detection logic against synthetic fixtures and assert the real repository
is currently in sync.

The guard lives under ``scripts/``, so it is added to ``sys.path`` the same way
:mod:`tests.test_check_readme_version` does.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import check_security_policy  # noqa: E402  (import after sys.path manipulation)


def test_read_pyproject_version_scoped_to_project_table(tmp_path: Path) -> None:
    """A ``version`` in another table must not shadow the [project] version."""
    pyproject = tmp_path / "pyproject.toml"
    pyproject.write_text(
        '[build-system]\nrequires = ["setuptools"]\nversion = "9.9.9"\n\n'
        '[project]\nname = "x"\nversion = "1.2.3"\n',
        encoding="utf-8",
    )
    assert check_security_policy.read_pyproject_version(pyproject) == "1.2.3"


def test_current_minor() -> None:
    """The MAJOR.MINOR series is derived from a full version string."""
    assert check_security_policy.current_minor("0.16.0") == "0.16"
    assert check_security_policy.current_minor("1.2.3rc1") == "1.2"


def test_find_supported_drift_in_sync() -> None:
    """No drift when the table marks the current minor (``.x`` form) supported."""
    text = "| Version | Supported |\n|--|--|\n| 0.16.x | Yes |\n| < 0.16 | No |\n"
    assert check_security_policy.find_supported_drift("0.16.0", text) == []


def test_find_supported_drift_flags_stale_minor() -> None:
    """A stale supported series (e.g. 0.14.x while package is 0.16.0) is flagged."""
    text = "| Version | Supported |\n|--|--|\n| 0.14.x | Yes |\n| < 0.14 | No |\n"
    problems = check_security_policy.find_supported_drift("0.16.0", text)
    assert len(problems) == 2  # current not supported + stale series still 'Yes'
    assert any("0.16" in p for p in problems)
    assert any("0.14" in p for p in problems)


def test_find_supported_drift_flags_missing_table() -> None:
    """A SECURITY.md with no recognisable support rows is flagged."""
    problems = check_security_policy.find_supported_drift("0.16.0", "no table here")
    assert problems == ["SECURITY.md has no recognisable 'Supported Versions' table rows."]


def test_find_broken_links_flags_missing_target(tmp_path: Path) -> None:
    """A repo-relative link to a nonexistent file is flagged; URLs are skipped."""
    security = tmp_path / "SECURITY.md"
    (tmp_path / "exists.md").write_text("ok", encoding="utf-8")
    security.write_text(
        "See [present](exists.md) and [absent](docs/missing.md).\n"
        "External [site](https://example.com) and [anchor](#scope) are skipped.\n",
        encoding="utf-8",
    )
    problems = check_security_policy.find_broken_links(security)
    assert problems == ["SECURITY.md links to 'docs/missing.md', which does not exist."]


def test_find_broken_links_resolves_anchor_targets(tmp_path: Path) -> None:
    """A link with a ``#fragment`` resolves against the file part only."""
    security = tmp_path / "SECURITY.md"
    (tmp_path / "page.md").write_text("ok", encoding="utf-8")
    security.write_text("[x](page.md#section)\n", encoding="utf-8")
    assert check_security_policy.find_broken_links(security) == []


def test_repository_security_policy_is_in_sync() -> None:
    """The live SECURITY.md must match the package version and have no dead links."""
    assert check_security_policy.main([]) == 0

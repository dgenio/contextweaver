"""Released tags and PyPI must agree (see scripts/check_published_versions.py).

Between 0.16.0 and 0.18.2, three tagged releases were advertised on GitHub while
PyPI served none of them, for up to six weeks, because nothing compared the two
lists. These tests cover the reconciliation itself; they never touch the network.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import check_published_versions as checker  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[1]


def _fetcher(releases: dict[str, list[dict]]) -> checker.Fetcher:
    def fetch(url: str) -> bytes:
        assert url == checker.PYPI_JSON
        return json.dumps({"releases": releases}).encode()

    return fetch


class TestReconcile:
    def test_a_tag_missing_from_pypi_is_reported(self) -> None:
        unexplained, stale = checker.reconcile({"1.0.0", "1.1.0"}, {"1.0.0"}, {})
        assert unexplained == {"1.1.0"}
        assert stale == set()

    def test_a_recorded_gap_is_accepted(self) -> None:
        unexplained, stale = checker.reconcile(
            {"1.0.0", "1.1.0"}, {"1.0.0"}, {"1.1.0": "publish workflow never ran"}
        )
        assert unexplained == set()
        assert stale == set()

    def test_an_exemption_that_is_now_published_is_reported(self) -> None:
        """A stale exemption would silently absorb the next real gap."""
        unexplained, stale = checker.reconcile(
            {"1.0.0"}, {"1.0.0"}, {"1.0.0": "never shipped — but it did"}
        )
        assert stale == {"1.0.0"}

    def test_pypi_versions_with_no_files_do_not_count_as_published(self) -> None:
        """Every file yanked means `pip install` fails; that is not published."""
        published = checker.published_versions(_fetcher({"1.0.0": [{"f": 1}], "1.1.0": []}))
        assert published == {"1.0.0"}

    def test_extra_pypi_versions_without_a_tag_are_not_an_error(self) -> None:
        """One-off uploads are the maintainer's business, not a release gap."""
        unexplained, stale = checker.reconcile({"1.0.0"}, {"1.0.0", "0.9.9"}, {})
        assert unexplained == set()
        assert stale == set()


class TestTagDiscovery:
    def test_only_immutable_release_tags_are_reconciled(self, tmp_path: Path) -> None:
        subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
        subprocess.run(
            ["git", "commit", "-q", "--allow-empty", "-m", "x"],
            cwd=tmp_path,
            check=True,
            env=_git_env(),
        )
        for tag in ("v1.0.0", "v1.1.0", "v2.0.0rc1", "nightly", "v1.2"):
            subprocess.run(["git", "tag", tag], cwd=tmp_path, check=True)

        assert checker.released_tags(tmp_path) == {"1.0.0", "1.1.0"}


def _git_env() -> dict[str, str]:
    import os

    env = dict(os.environ)
    env.update(
        GIT_AUTHOR_NAME="t",
        GIT_AUTHOR_EMAIL="t@e",
        GIT_COMMITTER_NAME="t",
        GIT_COMMITTER_EMAIL="t@e",
    )
    return env


class TestCommittedExemptions:
    def test_the_file_parses_and_every_entry_carries_a_reason(self) -> None:
        exemptions = checker.load_exemptions()
        assert exemptions, "no exemptions recorded — the three known gaps should be here"
        for version, reason in exemptions.items():
            assert len(reason) > 40, f"{version} has no usable reason: {reason!r}"

    def test_the_comment_key_is_not_treated_as_a_version(self) -> None:
        raw = json.loads(checker.DEFAULT_EXEMPTIONS.read_text(encoding="utf-8"))
        assert "_comment" in raw, "the file should explain itself to the next reader"
        assert "_comment" not in checker.load_exemptions()

    def test_the_known_gaps_are_the_three_measured_ones(self) -> None:
        assert set(checker.load_exemptions()) == {"0.17.0", "0.18.0", "0.18.1"}


class TestNotWiredIntoTheOfflineGate:
    def test_make_ci_does_not_depend_on_pypi(self) -> None:
        """A local gate must not fail because pypi.org is unreachable."""
        makefile = (REPO_ROOT / "Makefile").read_text(encoding="utf-8")
        ci_line = next(line for line in makefile.splitlines() if line.startswith("ci:"))
        assert "published-versions" not in ci_line

    def test_the_scheduled_workflow_checks_out_tags(self) -> None:
        """Without tags the script has nothing to reconcile."""
        workflow = (REPO_ROOT / ".github/workflows/release-readiness.yml").read_text(
            encoding="utf-8"
        )
        assert "check_published_versions.py" in workflow, "the check is not wired anywhere"
        assert "fetch-tags: true" in workflow, (
            "the job running the check must fetch tags, or it reconciles an empty set"
        )


@pytest.mark.parametrize("argv", [[], ["--json"]])
def test_main_reports_a_clean_repository(monkeypatch: pytest.MonkeyPatch, argv: list[str]) -> None:
    monkeypatch.setattr(checker, "released_tags", lambda *a, **k: {"1.0.0"})
    monkeypatch.setattr(checker, "published_versions", lambda *a, **k: {"1.0.0"})
    monkeypatch.setattr(checker, "load_exemptions", lambda *a, **k: {})

    assert checker.main(argv) == 0


def test_main_fails_loudly_when_no_tags_are_present(monkeypatch: pytest.MonkeyPatch) -> None:
    """An empty tag list must not pass as 'nothing to reconcile'."""
    monkeypatch.setattr(checker, "released_tags", lambda *a, **k: set())

    assert checker.main([]) == 1

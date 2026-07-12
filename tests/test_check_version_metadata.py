"""Tests for the server.json / CITATION.cff version-drift guard (#747).

Pins the detection + sync logic (synthetic drift / in-sync cases that are
awkward to reproduce against the live files) and asserts the real repository is
currently in sync. The guard lives under ``scripts/``, so it is added to
``sys.path`` the same way :mod:`tests.test_check_readme_version` does.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import check_version_metadata  # noqa: E402  (import after sys.path manipulation)


def test_real_repo_metadata_is_in_sync() -> None:
    """The committed server.json / CITATION.cff match the package version."""
    assert check_version_metadata.main([]) == 0


def test_read_server_versions_finds_both_fields(tmp_path: Path) -> None:
    server = tmp_path / "server.json"
    server.write_text(
        '{\n  "version": "1.2.3",\n  "packages": [{"version": "1.2.3"}]\n}\n',
        encoding="utf-8",
    )
    assert check_version_metadata.read_server_versions(server) == ["1.2.3", "1.2.3"]


def test_read_citation_version_strips_quotes(tmp_path: Path) -> None:
    citation = tmp_path / "CITATION.cff"
    citation.write_text('title: x\nversion: 1.2.3\ndate-released: "2026-01-01"\n', encoding="utf-8")
    assert check_version_metadata.read_citation_version(citation) == "1.2.3"


def test_find_drift_flags_stale_server_and_citation(tmp_path: Path) -> None:
    server = tmp_path / "server.json"
    server.write_text('{"version": "0.1.0", "packages": [{"version": "0.1.0"}]}', encoding="utf-8")
    citation = tmp_path / "CITATION.cff"
    citation.write_text("version: 0.2.0\n", encoding="utf-8")
    problems = check_version_metadata.find_drift("1.2.3", server, citation)
    # Two server fields + one citation field are stale.
    assert len(problems) == 3
    assert all("expected '1.2.3'" in p for p in problems)


def test_find_drift_empty_when_in_sync(tmp_path: Path) -> None:
    server = tmp_path / "server.json"
    server.write_text('{"version": "1.2.3", "packages": [{"version": "1.2.3"}]}', encoding="utf-8")
    citation = tmp_path / "CITATION.cff"
    citation.write_text('version: 1.2.3\ndate-released: "2026-01-01"\n', encoding="utf-8")
    assert check_version_metadata.find_drift("1.2.3", server, citation) == []


def test_sync_server_json_rewrites_versions_preserving_format(tmp_path: Path) -> None:
    server = tmp_path / "server.json"
    original = '{\n  "name": "x",\n  "version": "0.1.0",\n  "packages": [{"version": "0.1.0"}]\n}\n'
    server.write_text(original, encoding="utf-8")

    changed = check_version_metadata.sync_server_json("1.2.3", server)
    assert changed is True
    assert check_version_metadata.read_server_versions(server) == ["1.2.3", "1.2.3"]
    # Non-version content is untouched.
    assert '"name": "x"' in server.read_text(encoding="utf-8")

    # Idempotent: a second sync reports no change.
    assert check_version_metadata.sync_server_json("1.2.3", server) is False

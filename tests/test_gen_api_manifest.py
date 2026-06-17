"""Tests for the public-API manifest generator and gate (issue #518).

The manifest is a committed, signature-level snapshot of the public surface;
``--check`` is the production drift gate. These tests pin determinism and the
drift contract without depending on the committed file's exact contents.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import gen_api_manifest  # noqa: E402  (import after sys.path manipulation)


def test_render_is_deterministic() -> None:
    assert gen_api_manifest.render_manifest() == gen_api_manifest.render_manifest()


def test_render_covers_every_public_module() -> None:
    manifest = gen_api_manifest.render_manifest()
    for module_name in gen_api_manifest.PUBLIC_MODULES:
        assert f"## {module_name}" in manifest
    # A couple of stable, well-known public symbols must appear with signatures.
    assert "class ContextManager" in manifest
    assert "ContextPack" in manifest


def test_check_passes_against_committed_manifest() -> None:
    """The committed manifest must match the current public surface."""
    assert gen_api_manifest.main(["--check"]) == 0


def test_check_detects_drift(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    stale = tmp_path / "public_api.txt"
    stale.write_text("# stale snapshot\n", encoding="utf-8")
    monkeypatch.setattr(gen_api_manifest, "MANIFEST_PATH", stale)
    assert gen_api_manifest.main(["--check"]) == 1


def test_regenerate_then_check_roundtrips(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "public_api.txt"
    monkeypatch.setattr(gen_api_manifest, "MANIFEST_PATH", target)
    assert gen_api_manifest.main([]) == 0
    assert target.exists()
    assert gen_api_manifest.main(["--check"]) == 0

"""Tests for ``contextweaver.data._paths`` (PR #301 review follow-up).

The previous implementation gated the zipimport branch on a ``TypeError``
that ``Path(str(...))`` does not actually raise, so it silently returned a
useless relative path for zipped wheels. These tests pin the post-fix
contract:

1. For editable / unpacked installs (where ``importlib.resources.files``
   returns a real :class:`pathlib.Path`), the function returns the same
   path unchanged and the file exists on disk.
2. For zipimport (simulated by feeding a non-``Path`` traversable through
   the helper), the function materialises the resource into a persistent
   cache directory under :func:`tempfile.gettempdir`.
"""

from __future__ import annotations

import importlib.resources as _resources
import shutil
import tempfile
from collections.abc import Generator
from pathlib import Path

import pytest

from contextweaver.data import gateway_catalog_path
from contextweaver.data._paths import GATEWAY_CATALOG_FILENAME


def test_returns_real_path_for_editable_install() -> None:
    """In an editable / unpacked-wheel install the resolver should hand
    back the on-disk ``Path`` directly — no copy, no temp file."""
    result = gateway_catalog_path()
    assert isinstance(result, Path)
    assert result.is_file(), f"resolved path {result} does not exist on disk"
    assert result.name == GATEWAY_CATALOG_FILENAME


def test_returned_path_outlives_function_call() -> None:
    """Calling the helper a second time must still produce a usable Path
    (the previous implementation returned a Path to a temp file that
    could be cleaned up on context-manager exit)."""
    first = gateway_catalog_path()
    second = gateway_catalog_path()
    assert first == second
    assert first.read_text(encoding="utf-8") == second.read_text(encoding="utf-8")


def test_returned_path_is_readable() -> None:
    """The resolved catalog must be readable YAML — the firewall demo and
    the ``mcp-gateway-full`` scenario fail loudly if it isn't."""
    contents = gateway_catalog_path().read_text(encoding="utf-8")
    assert contents.strip(), "catalog file is empty"
    # The packaged catalog is YAML and lists tool entries; smoke-check that
    # it is parseable rather than re-validating the schema here.
    assert "id:" in contents or "name:" in contents


def test_zipimport_fallback_uses_persistent_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """Simulate the zipimport branch by replacing the real on-disk
    traversable with a non-``Path`` wrapper. The helper must extract the
    asset to a persistent cache (not a context-manager-scoped temp file)
    so the returned ``Path`` stays valid after the helper returns."""
    real_traversable = _resources.files("contextweaver.data").joinpath(GATEWAY_CATALOG_FILENAME)
    assert isinstance(real_traversable, Path), (
        "test prerequisite: editable install must expose a real Path; "
        "without one the simulated zipimport wrapper cannot delegate "
        "as_file() back to a working source"
    )

    class _NotAPath:
        """Quack-types a Traversable without inheriting from ``Path``."""

        def __init__(self, source: Path) -> None:
            self._source = source

        def __fspath__(self) -> str:  # pragma: no cover — defensive
            return str(self._source)

        def __str__(self) -> str:
            # Mimic the relative-path string a ZipPath would return, which
            # is precisely what made the previous implementation buggy.
            return f"contextweaver/data/{self._source.name}"

    wrapped = _NotAPath(real_traversable)

    # ``as_file`` accepts plain Traversables via a registered context
    # manager; for the wrapper we route through a real ``Path`` so the
    # ``shutil.copyfile`` call in the helper succeeds.
    import contextlib

    @contextlib.contextmanager
    def _fake_as_file(traversable: object) -> Generator[Path, None, None]:
        assert traversable is wrapped
        yield real_traversable

    monkeypatch.setattr(
        "contextweaver.data._paths._resources.files",
        lambda _: type("_Joinable", (), {"joinpath": lambda _self, _name: wrapped})(),
    )
    monkeypatch.setattr("contextweaver.data._paths._resources.as_file", _fake_as_file)

    # Make sure no stale cache file is present from a prior run.
    cache_dir = Path(tempfile.gettempdir()) / "contextweaver"
    cached = cache_dir / GATEWAY_CATALOG_FILENAME
    if cached.exists():
        cached.unlink()
    try:
        result = gateway_catalog_path()
        assert isinstance(result, Path)
        assert result == cached
        assert result.is_file(), "cached catalog was not materialised"
        # Returned Path must outlive the helper call — read it back.
        assert result.read_text(encoding="utf-8") == real_traversable.read_text(encoding="utf-8")

        # Calling the helper again hits the cached file (no re-copy needed).
        again = gateway_catalog_path()
        assert again == result
    finally:
        if cached.exists():
            cached.unlink()
        # Best-effort cache-dir cleanup; ignore if other tests left files.
        if cache_dir.exists():
            shutil.rmtree(cache_dir, ignore_errors=True)

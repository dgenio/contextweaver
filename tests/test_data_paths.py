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
from collections.abc import Generator
from pathlib import Path

import pytest

from contextweaver.data import gateway_catalog_path
from contextweaver.data._paths import (
    GATEWAY_CATALOG_FILENAME,
    _owned_by_current_user,
    _user_cache_dir,
)


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


def test_user_cache_dir_prefers_xdg(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """``XDG_CACHE_HOME`` wins and lands under a ``contextweaver`` subdir (#742)."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    assert _user_cache_dir() == tmp_path / "contextweaver"


def test_owned_by_current_user_true_for_self_created(tmp_path: Path) -> None:
    """A file the test process just created is owned by the current user."""
    f = tmp_path / "x"
    f.write_text("data", encoding="utf-8")
    assert _owned_by_current_user(f) is True


def test_owned_by_current_user_false_for_foreign_uid(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A file whose ``st_uid`` differs from the current uid is not trusted."""
    import os

    if not hasattr(os, "getuid"):  # pragma: no cover — non-POSIX
        pytest.skip("ownership check is POSIX-only")
    f = tmp_path / "x"
    f.write_text("data", encoding="utf-8")
    monkeypatch.setattr(os, "getuid", lambda: f.stat().st_uid + 1)
    assert _owned_by_current_user(f) is False


def test_zipimport_foreign_owned_cache_is_rematerialised(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A pre-existing but foreign-owned cache file is overwritten, not trusted (#742)."""
    import os

    if not hasattr(os, "getuid"):  # pragma: no cover — non-POSIX
        pytest.skip("ownership check is POSIX-only")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    real = _resources.files("contextweaver.data").joinpath(GATEWAY_CATALOG_FILENAME)
    assert isinstance(real, Path)

    cache_dir = tmp_path / "contextweaver"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / GATEWAY_CATALOG_FILENAME
    cached.write_text("POISONED", encoding="utf-8")

    class _NotAPath:
        def __str__(self) -> str:
            return f"contextweaver/data/{GATEWAY_CATALOG_FILENAME}"

    wrapped = _NotAPath()

    import contextlib

    @contextlib.contextmanager
    def _fake_as_file(traversable: object) -> Generator[Path, None, None]:
        yield real

    monkeypatch.setattr(
        "contextweaver.data._paths._resources.files",
        lambda _: type("_J", (), {"joinpath": lambda _s, _n: wrapped})(),
    )
    monkeypatch.setattr("contextweaver.data._paths._resources.as_file", _fake_as_file)
    # Force the ownership check to treat the existing cache file as foreign.
    monkeypatch.setattr("contextweaver.data._paths._owned_by_current_user", lambda _p: False)

    result = gateway_catalog_path()
    assert result == cached
    assert result.read_text(encoding="utf-8") == real.read_text(encoding="utf-8")
    assert "POISONED" not in result.read_text(encoding="utf-8")


def test_zipimport_fallback_uses_persistent_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Simulate the zipimport branch by replacing the real on-disk
    traversable with a non-``Path`` wrapper. The helper must extract the
    asset to a persistent per-user cache (not a context-manager-scoped temp
    file) so the returned ``Path`` stays valid after the helper returns."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
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

    # The per-user cache dir is isolated under the monkeypatched XDG root.
    cache_dir = tmp_path / "contextweaver"
    cached = cache_dir / GATEWAY_CATALOG_FILENAME
    result = gateway_catalog_path()
    assert isinstance(result, Path)
    assert result == cached
    assert result.is_file(), "cached catalog was not materialised"
    # Returned Path must outlive the helper call — read it back.
    assert result.read_text(encoding="utf-8") == real_traversable.read_text(encoding="utf-8")

    # Calling the helper again hits the cached file (no re-copy needed).
    again = gateway_catalog_path()
    assert again == result

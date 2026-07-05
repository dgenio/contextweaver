"""Path resolvers for packaged data files (issue #264).

Pulled into its own module to keep ``contextweaver.data.__init__`` a pure
re-export surface per the project's "no business logic in ``__init__.py``"
hard rule.
"""

from __future__ import annotations

import importlib.resources as _resources
import os
import shutil
import tempfile
from pathlib import Path

GATEWAY_CATALOG_FILENAME = "mcp_gateway_catalog.yaml"


def _user_cache_dir() -> Path:
    """Return a per-user cache directory for materialised packaged data.

    Prefers the XDG base-directory spec (``$XDG_CACHE_HOME`` then ``~/.cache``)
    so the cache lives in a directory the invoking user owns — unlike a shared,
    world-writable ``tempfile.gettempdir()/contextweaver/`` where another local
    user could pre-create or swap the cached file (catalog-poisoning / TOCTOU
    risk, issue #742).  Falls back to a per-user-named subdirectory under the
    system temp dir only when no home/XDG location is resolvable.
    """
    xdg = os.environ.get("XDG_CACHE_HOME")
    base = Path(xdg) if xdg else Path.home() / ".cache"
    try:
        return base / "contextweaver"
    except (RuntimeError, OSError):  # pragma: no cover — home unresolvable
        # Namespaced by uid so it is not a shared, guessable path.
        uid = getattr(os, "getuid", lambda: "user")()
        return Path(tempfile.gettempdir()) / f"contextweaver-{uid}"


def _owned_by_current_user(path: Path) -> bool:
    """Return ``True`` when *path* is owned by the current user (POSIX).

    On platforms without ``os.getuid`` (e.g. Windows) ownership is assumed —
    those filesystems do not expose the shared-``/tmp`` TOCTOU surface this
    check defends against.
    """
    getuid = getattr(os, "getuid", None)
    if getuid is None:  # pragma: no cover — non-POSIX
        return True
    try:
        return bool(path.stat().st_uid == getuid())
    except OSError:  # pragma: no cover — race: file vanished
        return False


def gateway_catalog_path() -> Path:
    """Return the on-disk path to the MCP gateway demo catalog.

    Resolves the packaged resource and returns a concrete
    :class:`pathlib.Path` that stays valid after this function returns.

    - **Editable installs and unpacked wheels** — ``importlib.resources.files``
      returns a real :class:`pathlib.Path`; we hand it back as-is.
    - **Zipped wheels / zipimport** — the traversable is a virtual
      ``MultiplexedPath`` / ``ZipPath``; :func:`importlib.resources.as_file`
      materialises a temporary file that is cleaned up when its context
      manager exits, so we copy the bytes into a persistent, **per-user**
      cache directory (:func:`_user_cache_dir`) and return that. A cached
      copy is reused only when the current user owns it (:func:`_owned_by_current_user`);
      a foreign-owned file is re-materialised rather than trusted. The cache is
      keyed on filename only — overwriting on every call would defeat its
      purpose, so we trust the wheel's content to be stable per process.

    Returns:
        :class:`pathlib.Path` pointing to a readable ``mcp_gateway_catalog.yaml``.
    """
    traversable = _resources.files("contextweaver.data").joinpath(GATEWAY_CATALOG_FILENAME)
    if isinstance(traversable, Path):
        return traversable
    cache_dir = _user_cache_dir()
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / GATEWAY_CATALOG_FILENAME
    if not cached.exists() or not _owned_by_current_user(cached):  # pragma: no cover — zipimport
        with _resources.as_file(traversable) as concrete:
            shutil.copyfile(concrete, cached)
    return cached

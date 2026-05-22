"""Path resolvers for packaged data files (issue #264).

Pulled into its own module to keep ``contextweaver.data.__init__`` a pure
re-export surface per the project's "no business logic in ``__init__.py``"
hard rule.
"""

from __future__ import annotations

import importlib.resources as _resources
import shutil
import tempfile
from pathlib import Path

GATEWAY_CATALOG_FILENAME = "mcp_gateway_catalog.yaml"


def gateway_catalog_path() -> Path:
    """Return the on-disk path to the MCP gateway demo catalog.

    Resolves the packaged resource and returns a concrete
    :class:`pathlib.Path` that stays valid after this function returns.

    - **Editable installs and unpacked wheels** — ``importlib.resources.files``
      returns a real :class:`pathlib.Path`; we hand it back as-is.
    - **Zipped wheels / zipimport** — the traversable is a virtual
      ``MultiplexedPath`` / ``ZipPath``; :func:`importlib.resources.as_file`
      materialises a temporary file that is cleaned up when its context
      manager exits, so we copy the bytes into a persistent location
      under ``tempfile.gettempdir()`` and return that. The cache is keyed
      on filename only — overwriting on every call would defeat its
      purpose, so we trust the wheel's content to be stable per process.

    Returns:
        :class:`pathlib.Path` pointing to a readable ``mcp_gateway_catalog.yaml``.
    """
    traversable = _resources.files("contextweaver.data").joinpath(GATEWAY_CATALOG_FILENAME)
    if isinstance(traversable, Path):
        return traversable
    cache_dir = Path(tempfile.gettempdir()) / "contextweaver"
    cache_dir.mkdir(parents=True, exist_ok=True)
    cached = cache_dir / GATEWAY_CATALOG_FILENAME
    if not cached.exists():  # pragma: no cover — only triggers under zipimport
        with _resources.as_file(traversable) as concrete:
            shutil.copyfile(concrete, cached)
    return cached

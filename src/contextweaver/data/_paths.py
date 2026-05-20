"""Path resolvers for packaged data files (issue #264).

Pulled into its own module to keep ``contextweaver.data.__init__`` a pure
re-export surface per the project's "no business logic in ``__init__.py``"
hard rule.
"""

from __future__ import annotations

import importlib.resources as _resources
from pathlib import Path

GATEWAY_CATALOG_FILENAME = "mcp_gateway_catalog.yaml"


def gateway_catalog_path() -> Path:
    """Return the on-disk path to the MCP gateway demo catalog.

    Resolves the packaged resource and converts it to a concrete
    :class:`pathlib.Path`. Works for both editable installs (where
    ``Traversable`` is already a ``PosixPath``) and zipped wheel installs
    (where :func:`importlib.resources.files` returns a virtual
    ``MultiplexedPath`` / ``ZipPath`` — in that case we materialise the
    asset via :func:`importlib.resources.as_file`).

    Returns:
        Absolute :class:`pathlib.Path` to ``mcp_gateway_catalog.yaml``.
    """
    traversable = _resources.files("contextweaver.data").joinpath(GATEWAY_CATALOG_FILENAME)
    try:
        # Editable install / unpacked wheel: ``Traversable`` is already a
        # real ``PosixPath``; ``Path(str(...))`` is effectively a no-op.
        return Path(str(traversable))
    except TypeError:  # pragma: no cover — only triggers under zipimport
        with _resources.as_file(traversable) as concrete:
            return Path(concrete)

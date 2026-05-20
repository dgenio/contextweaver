"""Version derivation helper — re-exported by __init__.py."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _pkg_version

try:
    __version__ = _pkg_version("contextweaver")
except PackageNotFoundError:
    # Running from a source tree without an installed distribution.
    __version__ = "0.0.0+local"

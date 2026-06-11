"""Private filesystem helpers for :mod:`contextweaver.store.json_file_artifacts`.

Extracted to keep the store module under the 300-line ceiling (issue #497).
Holds the on-disk naming constants, handle validation + filename encoding
(issue #466), and the atomic-write primitive.  Not public API.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path
from urllib.parse import quote

from contextweaver.exceptions import ContextWeaverError

META_SUFFIX = ".json"
DATA_SUFFIX = ".data"
_TMP_PREFIX = "._cw_tmp_"
_FORBIDDEN_HANDLE_CHARS: frozenset[str] = frozenset({"/", "\\", "\x00"})


def validate_handle(handle: str) -> None:
    """Reject handles that would escape ``base_dir`` or contain path separators."""
    if not handle:
        raise ContextWeaverError("Artifact handle must be non-empty")
    if handle in {".", ".."}:
        raise ContextWeaverError(f"Invalid artifact handle: {handle!r}")
    if any(ch in handle for ch in _FORBIDDEN_HANDLE_CHARS):
        raise ContextWeaverError(
            f"Invalid artifact handle (contains path separator or null byte): {handle!r}"
        )


def encode_handle(handle: str) -> str:
    """Percent-encode *handle* into a portable, collision-free filename stem.

    ``quote(safe="")`` keeps the ASCII alphanumerics plus ``_.-~`` verbatim
    (so simple handles like ``h1`` are unchanged and stay grep-friendly) and
    encodes everything else — crucially ``:`` -> ``%3A`` — so the stem is a
    valid filename on every platform (an unencoded ``:`` opens an NTFS
    alternate data stream on Windows).  The mapping is injective, so distinct
    handles never share a file.
    """
    return quote(handle, safe="")


def atomic_write(path: Path, data: bytes) -> None:
    """Write *data* to *path* atomically (temp file in the same dir + replace).

    :func:`os.replace` is atomic on a single filesystem, so a reader never
    observes a half-written or truncated file and a crash mid-write leaves the
    previous version intact.
    """
    fd, tmp = tempfile.mkstemp(prefix=_TMP_PREFIX, dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise

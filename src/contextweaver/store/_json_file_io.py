"""Private filesystem helpers for :mod:`contextweaver.store.json_file_artifacts`.

Extracted to keep the store module under the 300-line ceiling (issue #497).
Holds the on-disk naming constants, handle validation + filename encoding
(issue #466), and the atomic-write primitive.  Not public API.
"""

from __future__ import annotations

import os
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING
from urllib.parse import quote

from contextweaver.exceptions import ContextWeaverError

if TYPE_CHECKING:
    from contextweaver.types import ArtifactRef

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


def consistent_data_size(base_dir: Path, meta_name: str, ref: ArtifactRef) -> int | None:
    """Return the on-disk data size for *ref* if its file pair is self-consistent.

    Used by ``JsonFileArtifactStore`` when rebuilding its in-memory index from
    disk (issue #497 review): only index a metadata file that genuinely
    corresponds to a retrievable artifact, so ``ref()`` / ``list_refs()`` never
    advertise a handle ``get()`` cannot serve and the quota byte counter is not
    inflated by orphan or mismatched metadata.

    A pair is consistent when the handle is valid, the metadata filename is
    exactly ``encode_handle(handle).json``, and the sibling ``.data`` file
    exists.  Returns that ``.data`` file's actual byte size, or ``None`` when
    any check fails (the caller skips the entry).
    """
    try:
        validate_handle(ref.handle)
    except ContextWeaverError:
        return None
    if meta_name != f"{encode_handle(ref.handle)}{META_SUFFIX}":
        return None
    data_path = base_dir / f"{encode_handle(ref.handle)}{DATA_SUFFIX}"
    if not data_path.is_file():
        return None
    return data_path.stat().st_size


#: Retry budget for the atomic ``os.replace`` swap on Windows (issue #749).
_REPLACE_RETRIES = 10
_REPLACE_BACKOFF_START = 0.01
_REPLACE_BACKOFF_MAX = 0.5


def atomic_write(path: Path, data: bytes) -> None:
    """Write *data* to *path* atomically (temp file in the same dir + replace).

    :func:`os.replace` is atomic on a single filesystem, so a reader never
    observes a half-written or truncated file and a crash mid-write leaves the
    previous version intact.

    On Windows, ``os.replace`` raises :class:`PermissionError` (``WinError 5``)
    when another handle has the destination file open — Windows forbids
    replacing an open file, unlike POSIX where the rename always succeeds
    (issue #749). The condition is transient under concurrent readers, so the
    swap is retried with a short exponential backoff. On POSIX this raise never
    occurs, so the loop runs exactly once and behaviour is unchanged.
    """
    fd, tmp = tempfile.mkstemp(prefix=_TMP_PREFIX, dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        delay = _REPLACE_BACKOFF_START
        for attempt in range(_REPLACE_RETRIES):
            try:
                os.replace(tmp, path)
                return
            except PermissionError as exc:
                # Retry only the Windows "destination is open" case — access
                # denied (5) or sharing violation (32). A POSIX PermissionError
                # (``winerror`` is None) is a real, non-transient failure: don't
                # mask it or add backoff delay; re-raise immediately.
                if getattr(exc, "winerror", None) not in (5, 32):
                    raise
                if attempt == _REPLACE_RETRIES - 1:
                    raise
                time.sleep(delay)
                delay = min(delay * 2, _REPLACE_BACKOFF_MAX)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise

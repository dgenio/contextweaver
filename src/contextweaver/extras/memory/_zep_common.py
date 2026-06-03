"""Shared helpers for the Zep external-memory backend (issue #195).

Internal module backing :mod:`contextweaver.extras.memory.zep`.  It holds the
shared constants, the :class:`ZepBackendError` exception, the JSON/scan
helpers, the defensive payload-coercion helpers, and the common
:class:`_ZepStoreBase` so the public ``zep`` module stays within the repo's
per-module line budget (≤ 300 lines).

Like :mod:`contextweaver.extras.memory.zep`, importing this module requires the
``[zep]`` optional extra and raises a friendly :class:`ImportError` (with the
exact install hint) when it is missing.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from contextweaver.exceptions import ContextWeaverError

if TYPE_CHECKING:
    from zep_cloud.client import Zep


try:
    from zep_cloud.client import Zep as _Zep  # noqa: F401
except ImportError as _zep_import_err:  # pragma: no cover - exercised only when extra is missing
    raise ImportError(
        "Zep external-memory backend requires the [zep] extra. "
        "Install with: pip install 'contextweaver[zep]'"
    ) from _zep_import_err


_CW_KIND = "cw_kind"
_CW_EPISODE_ID = "cw_episode_id"
_CW_FACT_ID = "cw_fact_id"
_CW_FACT_KEY = "cw_key"
_KIND_EPISODE = "episode"
_KIND_FACT = "fact"
_DEFAULT_SCAN_LIMIT = 1000


class ZepBackendError(ContextWeaverError):
    """Raised when the Zep backend cannot honour a store operation."""


def _coerce_str_tags(value: object) -> list[str]:
    """Coerce a scanned ``tags`` payload field into a list of strings.

    Episodes scanned out of an existing Zep deployment may carry a malformed
    ``tags`` field — e.g. a bare string (which a naive comprehension would
    iterate character-by-character) or some other non-list value.  Only a
    genuine list contributes, and only its string members.
    """
    if not isinstance(value, list):
        return []
    return [t for t in value if isinstance(t, str)]


def _coerce_metadata(value: object) -> dict[str, Any]:
    """Coerce a scanned ``metadata`` payload field into a dict.

    A non-dict ``metadata`` (which ``dict(...)`` would reject with a
    ``TypeError`` or silently mangle) yields an empty mapping so a stale or
    hand-edited Zep episode cannot corrupt ``Episode`` / ``Fact`` rebuilds.
    """
    return dict(value) if isinstance(value, dict) else {}


def _episode_records(raw: object) -> list[object]:
    """Coerce a ``graph.episode.get_by_user_id`` response into an episode list.

    Accepts both the SDK's response object (``.episodes``) and a bare list /
    ``{"episodes": [...]}`` dict for resilience across client versions.
    """
    if raw is None:
        return []
    episodes = getattr(raw, "episodes", None)
    if episodes is None and isinstance(raw, dict):
        episodes = raw.get("episodes")
    if episodes is None and isinstance(raw, list):
        episodes = raw
    return list(episodes) if episodes is not None else []


def _episode_uuid(record: object) -> str | None:
    """Return the Zep episode uuid of *record*, tolerating attr/key variants."""
    for attr in ("uuid_", "uuid", "id"):
        value = getattr(record, attr, None)
        if value is None and isinstance(record, dict):
            value = record.get(attr)
        if isinstance(value, str) and value:
            return value
    return None


def _episode_payload(record: object) -> dict[str, Any]:
    """Return the JSON payload contextweaver stored in a Zep episode's content."""
    content = getattr(record, "content", None)
    if content is None and isinstance(record, dict):
        content = record.get("content")
    if not isinstance(content, str):
        return {}
    try:
        loaded = json.loads(content)
    except (json.JSONDecodeError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


class _ZepStoreBase:
    """Shared scope, scan, and write helpers for the Zep stores."""

    def __init__(self, client: Zep, *, user_id: str, scan_limit: int) -> None:
        if not user_id:
            raise ZepBackendError(f"{type(self).__name__} requires a non-empty user_id.")
        self._client = client
        self._user_id = user_id
        self._scan_limit = scan_limit

    def _add_json(self, payload: dict[str, Any]) -> None:
        self._client.graph.add(
            user_id=self._user_id,
            type="json",
            data=json.dumps(payload),
        )

    def _scan(self) -> list[object]:
        raw = self._client.graph.episode.get_by_user_id(
            user_id=self._user_id, lastn=self._scan_limit
        )
        records = _episode_records(raw)
        if len(records) >= self._scan_limit:
            raise NotImplementedError(
                f"{type(self).__name__}: {self._user_id!r} scope has at least "
                f"{self._scan_limit} episodes; scanning ops are no longer "
                "lossless. Narrow scope via user_id partitioning or use a "
                "dedicated store backend."
            )
        return records

    def _delete_uuid(self, uuid: str) -> None:
        self._client.graph.episode.delete(uuid_=uuid)

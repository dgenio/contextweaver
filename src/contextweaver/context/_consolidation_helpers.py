"""Private deterministic helpers for the consolidation engine (issue #498).

Pure functions extracted from :mod:`contextweaver.context.consolidation` to keep
that module within the project's ≤300-line ceiling. Not public API — the
public surface is re-exported from ``consolidation``.
"""

from __future__ import annotations

import hashlib
from datetime import datetime

from contextweaver._utils import tokenize
from contextweaver.store.episodic import Episode
from contextweaver.types import Sensitivity

#: Fact key under which consolidated facts are stored.
CONSOLIDATED_FACT_KEY = "consolidated"

#: Severity ranking used to inherit the maximum sensitivity of source episodes.
_SENSITIVITY_RANK: dict[Sensitivity, int] = {
    Sensitivity.public: 0,
    Sensitivity.internal: 1,
    Sensitivity.confidential: 2,
    Sensitivity.restricted: 3,
}


def canonical_member(members: list[Episode]) -> str:
    """Return the deterministic representative summary for *members*.

    Picks the summary with the most tokens (most informative), breaking ties by
    the smallest ``episode_id`` so the choice is reproducible.
    """
    best = min(members, key=lambda ep: (-len(tokenize(ep.summary)), ep.episode_id))
    return best.summary


def max_sensitivity(members: list[Episode]) -> Sensitivity:
    """Return the highest sensitivity among *members* (defaults to public)."""
    return max(
        (ep.sensitivity for ep in members),
        key=lambda s: _SENSITIVITY_RANK[s],
        default=Sensitivity.public,
    )


def count_sessions(members: list[Episode], session_key: str) -> int:
    """Count distinct sessions in *members*.

    Episodes lacking a session marker collectively count as one shared session.
    """
    sessions: set[str] = set()
    for ep in members:
        value = ep.metadata.get(session_key)
        sessions.add(str(value) if value is not None else "\x00unscoped")
    return len(sessions)


def episode_iso(ep: Episode, key: str) -> str | None:
    """Return *ep*'s ISO-8601 timestamp metadata value, or ``None``."""
    value = ep.metadata.get(key)
    return value if isinstance(value, str) and value else None


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO-8601 *value* to a ``datetime``, or ``None`` on failure."""
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def seen_bounds(members: list[Episode], key: str) -> tuple[str | None, str | None]:
    """Return the (first_seen, last_seen) ISO timestamps across *members*."""
    stamped = [(iso, parse_iso(iso)) for ep in members if (iso := episode_iso(ep, key))]
    parsed = [(iso, dt) for iso, dt in stamped if dt is not None]
    if not parsed:
        return None, None
    first = min(parsed, key=lambda pair: pair[1])[0]
    last = max(parsed, key=lambda pair: pair[1])[0]
    return first, last


def canonical_fact_id(source_ids: list[str]) -> str:
    """Return a deterministic, content-addressed fact ID for *source_ids*."""
    digest = hashlib.sha1("\n".join(sorted(source_ids)).encode("utf-8")).hexdigest()[:12]
    return f"fact:{CONSOLIDATED_FACT_KEY}:{digest}"


def is_decayed(iso: str | None, as_of: datetime, decay_after_days: int) -> bool:
    """Return ``True`` when *iso* is older than *decay_after_days* before *as_of*."""
    stamp = parse_iso(iso)
    if stamp is None:
        return False
    return (as_of - stamp).days > decay_after_days


__all__ = [
    "CONSOLIDATED_FACT_KEY",
    "canonical_fact_id",
    "canonical_member",
    "count_sessions",
    "episode_iso",
    "is_decayed",
    "max_sensitivity",
    "parse_iso",
    "seen_bounds",
]

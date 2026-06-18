"""Private deterministic helpers for the consolidation engine (issue #498).

Pure functions extracted from :mod:`contextweaver.context.consolidation` to keep
that module within the project's ≤300-line ceiling. Not public API — the
public surface is re-exported from ``consolidation``.
"""

from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone

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


def coerce_iso(value: object) -> str | None:
    """Coerce a metadata timestamp *value* to ISO text, or ``None``.

    Accepts ISO-8601 strings and :class:`~datetime.datetime` values; any other
    type (or an empty string) is treated as undated.
    """
    if isinstance(value, datetime):
        return value.isoformat()
    return value if isinstance(value, str) and value else None


def _to_naive_utc(dt: datetime) -> datetime:
    """Return *dt* as a naive UTC datetime (tz-aware inputs are converted)."""
    if dt.tzinfo is not None:
        return dt.astimezone(timezone.utc).replace(tzinfo=None)
    return dt


def parse_iso(value: str | None) -> datetime | None:
    """Parse an ISO-8601 *value* to a naive-UTC ``datetime``, or ``None``.

    Mirrors the repo's ISO convention (e.g. ``RoutingDecision.from_dict``): the
    RFC 3339 ``Z`` UTC suffix is normalised to ``+00:00`` so timestamps parse on
    Python 3.10, and tz-aware values are converted to naive UTC so later
    arithmetic against a naive reference time never raises. Naive inputs are
    assumed to already be UTC.
    """
    if not value:
        return None
    text = value[:-1] + "+00:00" if value.endswith(("Z", "z")) else value
    try:
        return _to_naive_utc(datetime.fromisoformat(text))
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
    """Return ``True`` when *iso* is older than *decay_after_days* before *as_of*.

    Compares against a :class:`~datetime.timedelta` (not floored whole days) so a
    timestamp older than the horizon by under 24h still decays, and normalises
    *as_of* to naive UTC so a tz-aware ``as_of`` or stamp never raises.
    """
    stamp = parse_iso(iso)
    if stamp is None:
        return False
    return _to_naive_utc(as_of) - stamp > timedelta(days=decay_after_days)


__all__ = [
    "CONSOLIDATED_FACT_KEY",
    "canonical_fact_id",
    "canonical_member",
    "coerce_iso",
    "count_sessions",
    "episode_iso",
    "is_decayed",
    "max_sensitivity",
    "parse_iso",
    "seen_bounds",
]

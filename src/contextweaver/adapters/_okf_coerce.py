"""Private frontmatter value-coercion helpers for the knowledge-source adapters.

Split out of :mod:`._okf_io` (issues #736/#763/#767/#776) so that module stays
within the ≤300-line convention. Pure functions over opaque ``yaml.safe_load``
output — no dependency on the rest of the adapter family, so the layering is
strictly ``_okf_coerce`` ← ``_okf_io`` ← ``_okf_materialize`` ← public adapters.

``yaml.safe_load`` is permissive by design (issue #736): it yields strings,
numbers, bools, lists, and — for unquoted dates/timestamps — ``datetime``
objects, plus ``bytes`` for ``!!binary``. These helpers normalise those into
the shapes :class:`~contextweaver.adapters._okf_io.KnowledgeNode` expects and,
critically, into JSON-serialisable leaves so the "JSON-compatible dict"
contract of ``to_dict`` / ``ContextItem.metadata`` holds.
"""

from __future__ import annotations

import datetime
from typing import Any


def coerce_str_list(value: Any) -> list[str]:  # noqa: ANN401 -- opaque frontmatter value
    """Coerce a scalar-or-list frontmatter value into a list of strings."""
    if isinstance(value, list):
        return [str(v) for v in value]
    if value in (None, ""):
        return []
    return [str(value)]


def coerce_float(value: Any, default: float) -> float:  # noqa: ANN401 -- opaque value
    """Coerce a frontmatter value to ``float``, falling back to *default*."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def coerce_expires(value: Any) -> tuple[float | None, bool]:  # noqa: ANN401 -- opaque value
    """Coerce a frontmatter ``expires_at`` to ``(seconds_or_None, ok)``.

    ``None`` in means "no expiry" (``ok=True``). A value that cannot become a
    float — most commonly a natural YAML date like ``expires_at: 2026-12-31``,
    which ``yaml.safe_load`` parses to :class:`datetime.date` — returns
    ``(None, False)`` so the caller surfaces a diagnostic and the node stays
    *live* rather than being silently coerced to ``0.0`` (epoch = perpetually
    expired) and dropped without a trace.
    """
    if value is None:
        return None, True
    try:
        return float(value), True
    except (TypeError, ValueError):
        return None, False


def json_safe(value: Any) -> Any:  # noqa: ANN401 -- opaque frontmatter value
    """Coerce *value* into a JSON-serialisable form.

    Frontmatter is preserved verbatim as metadata, but ``yaml.safe_load``
    parses unquoted dates/timestamps into :class:`datetime.date` /
    :class:`datetime.datetime` and ``!!binary`` into :class:`bytes` — none of
    which :func:`json.dumps` accepts. Coercing those leaves here keeps the
    documented "JSON-compatible dict" contract of ``KnowledgeNode.to_dict`` and
    of the ``ContextItem.metadata`` these nodes materialise into (which the
    SQLite/Redis event-log stores ``json.dumps`` with no ``default=str``).
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, (datetime.date, datetime.time)):  # date covers datetime
        return value.isoformat()
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, set)):
        return [json_safe(v) for v in value]
    return str(value)


__all__ = ["coerce_expires", "coerce_float", "coerce_str_list", "json_safe"]

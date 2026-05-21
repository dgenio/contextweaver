"""Shared normalisation helpers for golden / contract / explain fixtures.

Strips volatile fields (timestamps, UUIDs, machine-specific data) from
serialised payloads before comparison so checked-in JSON fixtures stay
byte-stable across runs and machines.

Used by:

* ``tests/test_golden_prompts.py`` and ``tests/test_golden_mcp_ingestion.py``
  (issue #296) — route-prompt + MCP-ingestion regression snapshots.
* ``tests/test_weaver_spec_fixtures.py`` (issue #295) — payload-fixture
  contract validation.
* ``tests/test_context_explanation.py`` (issue #291) — explain-output
  determinism.
* ``tests/test_sensitivity_fixtures.py`` (issue #292) — sensitivity
  regression set.

Design notes
------------

* Pure stdlib — no contextweaver imports — so fixture tests can use this
  helper even when the surface under test is in an early failure state.
* Read-only on inputs: returns a fresh dict / list rather than mutating
  the caller's payload.
* Conservative: only canonicalises fields that are *known* to be volatile.
  Adding a new normalisation rule is a deliberate change.

Note on ``Any``: every public helper takes ``Any`` because it walks
arbitrary JSON-shaped payloads.  ANN401 is suppressed inline rather
than globally so unrelated test files cannot silently piggy-back on
the same exemption.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

# Field names whose *value* is a volatile timestamp / id.  Replaced with a
# constant sentinel so the rest of the payload can be compared byte-for-byte.
_VOLATILE_KEYS: frozenset[str] = frozenset(
    {
        "timestamp",
        "created_at",
        "updated_at",
        "trace_id",
    }
)

# Field names that hold UUID-shaped ids (``rd-<uuid4>``, ``msg-<uuid4>``).
# The prefix is preserved so failures still indicate the kind of id; the
# UUID portion is replaced with a constant sentinel.
_UUID_PREFIX_KEYS: frozenset[str] = frozenset(
    {
        "id",
        "decision_id",
        "frame_id",
        "selected_card_id",
        "selected_item_id",
        "request_id",
    }
)

_UUID_RE = re.compile(
    r"^([a-zA-Z]+-)?[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-"
    r"[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)

_TIMESTAMP_SENTINEL = "<timestamp>"
_UUID_SENTINEL = "<uuid>"


def normalize(
    payload: Any,  # noqa: ANN401 — walks arbitrary JSON
    *,
    round_floats: int | None = 4,
    drop_keys: Sequence[str] = (),
) -> Any:  # noqa: ANN401 — walks arbitrary JSON
    """Return a normalised, JSON-stable copy of *payload*.

    Volatile fields (timestamps, UUIDs) are replaced with constant
    sentinels and float scores are rounded so checked-in fixtures stay
    byte-stable across runs.

    Args:
        payload: Anything JSON-serialisable: dict, list, str, int, float,
            bool, None.
        round_floats: When set, all ``float`` leaves are rounded to this
            many decimal places.  Default ``4``.  Pass ``None`` to keep
            full precision (useful for explicit-precision fields).
        drop_keys: Additional dict keys to drop entirely.  Empty by
            default; callers can pass e.g. ``("retriever_engine",)`` to
            scrub host-specific labels.

    Returns:
        A fresh, normalised copy of *payload* with the same JSON shape.
    """
    drop = set(drop_keys)
    return _normalize(payload, round_floats=round_floats, drop=drop)


def _normalize(
    payload: Any,  # noqa: ANN401 — walks arbitrary JSON
    *,
    round_floats: int | None,
    drop: set[str],
) -> Any:  # noqa: ANN401 — walks arbitrary JSON
    if isinstance(payload, Mapping):
        out: dict[str, Any] = {}
        for key in sorted(payload):
            if key in drop:
                continue
            value = payload[key]
            if key in _VOLATILE_KEYS and value is not None:
                out[key] = _TIMESTAMP_SENTINEL
            elif key in _UUID_PREFIX_KEYS and isinstance(value, str) and _UUID_RE.match(value):
                # Preserve any human-readable prefix (e.g. ``rd-``) so a
                # failed-fixture diff still tells the reader which id
                # field changed.
                prefix_match = re.match(r"^([a-zA-Z]+-)", value)
                prefix = prefix_match.group(1) if prefix_match else ""
                out[key] = f"{prefix}{_UUID_SENTINEL}"
            else:
                out[key] = _normalize(value, round_floats=round_floats, drop=drop)
        return out
    if isinstance(payload, (list, tuple)):
        return [_normalize(v, round_floats=round_floats, drop=drop) for v in payload]
    if isinstance(payload, float) and round_floats is not None:
        return round(payload, round_floats)
    return payload


def to_canonical_json(payload: Any) -> str:  # noqa: ANN401 — walks arbitrary JSON
    """Return *payload* as canonical pretty JSON.

    Sorted keys, two-space indent, trailing newline.  Matching the layout
    used by every checked-in fixture under ``tests/fixtures/`` so
    ``diff`` and editor folding behave predictably.
    """
    return json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"


def load_fixture(path: Any) -> Any:  # noqa: ANN401 — walks arbitrary JSON
    """Load a checked-in JSON fixture from *path*.

    Args:
        path: A :class:`pathlib.Path` (or anything with a ``read_text``
            method) pointing at a checked-in fixture.

    Returns:
        The parsed JSON document.
    """
    return json.loads(path.read_text(encoding="utf-8"))

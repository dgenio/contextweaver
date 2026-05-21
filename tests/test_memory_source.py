"""Tests for contextweaver.context.memory_source (issue #293)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from contextweaver.config import ContextPolicy
from contextweaver.context.memory_source import (
    PHASE_SCOPE_PREFERENCES,
    JsonFixtureMemorySource,
    MemoryEntry,
    memory_entries_to_context_items,
    select_memory_for_phase,
)
from contextweaver.context.sensitivity import apply_sensitivity_filter
from contextweaver.exceptions import ConfigError
from contextweaver.protocols import MemorySource
from contextweaver.types import ItemKind, Phase, Sensitivity

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _entry(
    eid: str,
    text: str = "x",
    *,
    source: str = "",
    scope: str = "",
    sensitivity: Sensitivity = Sensitivity.public,
    confidence: float = 1.0,
    timestamp: float = 0.0,
    expires_at: float | None = None,
    tags: list[str] | None = None,
    metadata: dict[str, object] | None = None,
) -> MemoryEntry:
    return MemoryEntry(
        id=eid,
        text=text,
        source=source,
        scope=scope,
        sensitivity=sensitivity,
        confidence=confidence,
        timestamp=timestamp,
        expires_at=expires_at,
        tags=list(tags or []),
        metadata=dict(metadata or {}),
    )


# ---------------------------------------------------------------------------
# MemoryEntry
# ---------------------------------------------------------------------------


def test_memory_entry_round_trip_lossless() -> None:
    entry = _entry(
        "m1",
        "Recall the outage on 2026-04-12",
        scope="domain",
        sensitivity=Sensitivity.internal,
        confidence=0.8,
        timestamp=1_700_000_000.0,
        expires_at=1_800_000_000.0,
        tags=["incident", "p1"],
        metadata={"author": "alice"},
    )
    payload = entry.to_dict()
    rehydrated = MemoryEntry.from_dict(payload)
    assert rehydrated == entry
    # JSON-compatible (no exotic types leak)
    assert json.dumps(payload)


def test_memory_entry_defaults_round_trip() -> None:
    entry = MemoryEntry(id="m2", text="hi")
    rehydrated = MemoryEntry.from_dict(entry.to_dict())
    assert rehydrated == entry
    assert rehydrated.sensitivity == Sensitivity.public
    assert rehydrated.confidence == 1.0
    assert rehydrated.expires_at is None


def test_memory_entry_is_expired_respects_now() -> None:
    entry = _entry("m1", expires_at=100.0)
    assert entry.is_expired(now=200.0) is True
    assert entry.is_expired(now=50.0) is False


def test_memory_entry_no_expiry_never_expires() -> None:
    assert _entry("m1").is_expired(now=1e12) is False


# ---------------------------------------------------------------------------
# JsonFixtureMemorySource
# ---------------------------------------------------------------------------


def test_source_satisfies_protocol() -> None:
    source = JsonFixtureMemorySource()
    assert isinstance(source, MemorySource)


def test_source_rejects_duplicate_ids_in_constructor() -> None:
    with pytest.raises(ConfigError, match="duplicate"):
        JsonFixtureMemorySource([_entry("m1"), _entry("m1", "different text")])


def test_source_rejects_duplicate_ids_on_add() -> None:
    source = JsonFixtureMemorySource([_entry("m1")])
    with pytest.raises(ConfigError, match="m1"):
        source.add(_entry("m1"))


def test_source_select_phase_scope_boost_for_route() -> None:
    routing = _entry("routing", text="prefer tool A over tool B for refunds", scope="routing")
    domain = _entry("domain", text="customer accounts live in db1", scope="domain")
    source = JsonFixtureMemorySource([routing, domain])
    selected = source.select("anything", Phase.route)
    # Both included, but routing-scoped wins under route phase.
    assert [e.id for e in selected] == ["routing", "domain"]


def test_source_select_phase_scope_boost_for_interpret() -> None:
    routing = _entry("routing", text="prefer A", scope="routing")
    domain = _entry("domain", text="accounts in db1", scope="domain")
    source = JsonFixtureMemorySource([routing, domain])
    selected = source.select("anything", Phase.interpret)
    # Interpret prefers domain/fact/convention.
    assert [e.id for e in selected] == ["domain", "routing"]


def test_source_select_drops_expired_entries() -> None:
    fresh = _entry("fresh", text="still valid")
    stale = _entry("stale", text="expired memory", expires_at=100.0)
    source = JsonFixtureMemorySource([fresh, stale])
    selected = source.select("memory", Phase.answer, now=200.0)
    assert [e.id for e in selected] == ["fresh"]


def test_source_select_respects_max_entries() -> None:
    entries = [_entry(f"m{i}", text=f"memory {i}") for i in range(5)]
    source = JsonFixtureMemorySource(entries)
    selected = source.select("memory", Phase.answer, max_entries=3)
    assert len(selected) == 3


def test_source_select_deterministic_ordering() -> None:
    # Two entries with identical score: tie-break by ID ascending.
    a = _entry("alpha", text="x", scope="routing", confidence=1.0, timestamp=10.0)
    b = _entry("beta", text="x", scope="routing", confidence=1.0, timestamp=10.0)
    source = JsonFixtureMemorySource([b, a])  # insertion order is reversed
    first = source.select("x", Phase.route)
    second = source.select("x", Phase.route)
    assert [e.id for e in first] == ["alpha", "beta"]
    assert [e.id for e in first] == [e.id for e in second]


def test_source_select_token_overlap_lifts_score() -> None:
    relevant = _entry("rel", text="database outage rca")
    other = _entry("other", text="lorem ipsum dolor")
    source = JsonFixtureMemorySource([other, relevant])
    selected = source.select("database outage", Phase.answer)
    assert selected[0].id == "rel"


def test_source_select_recent_wins_on_confidence_tie() -> None:
    old = _entry("old", text="t", scope="routing", confidence=0.5, timestamp=100.0)
    new = _entry("new", text="t", scope="routing", confidence=0.5, timestamp=200.0)
    source = JsonFixtureMemorySource([old, new])
    selected = source.select("", Phase.route)
    assert [e.id for e in selected] == ["new", "old"]


def test_source_from_json_file_round_trip(tmp_path: Path) -> None:
    entries = [_entry("m1", text="one"), _entry("m2", text="two", scope="domain")]
    fixture = tmp_path / "memory.json"
    fixture.write_text(json.dumps([e.to_dict() for e in entries]))
    loaded = JsonFixtureMemorySource.from_json_file(fixture)
    assert [e.id for e in loaded.all()] == ["m1", "m2"]


def test_source_from_json_file_missing_raises(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="not found"):
        JsonFixtureMemorySource.from_json_file(tmp_path / "missing.json")


def test_source_from_json_file_bad_json_raises(tmp_path: Path) -> None:
    bad = tmp_path / "bad.json"
    bad.write_text("not json at all {")
    with pytest.raises(ConfigError, match="invalid JSON"):
        JsonFixtureMemorySource.from_json_file(bad)


def test_source_from_json_file_non_list_raises(tmp_path: Path) -> None:
    not_list = tmp_path / "obj.json"
    not_list.write_text('{"id": "m1"}')
    with pytest.raises(ConfigError, match="expected a JSON list"):
        JsonFixtureMemorySource.from_json_file(not_list)


# ---------------------------------------------------------------------------
# memory_entries_to_context_items
# ---------------------------------------------------------------------------


def test_materialise_preserves_sensitivity() -> None:
    entry = _entry("m1", text="secret", sensitivity=Sensitivity.restricted)
    [item] = memory_entries_to_context_items([entry])
    assert item.kind == ItemKind.memory_fact
    assert item.sensitivity == Sensitivity.restricted


def test_materialise_drops_expired_entries() -> None:
    items = memory_entries_to_context_items(
        [_entry("a"), _entry("b", expires_at=100.0)],
        now=200.0,
    )
    assert [i.id for i in items] == ["memory:a"]


def test_materialise_stamps_provenance_namespace() -> None:
    entry = _entry("m1", text="x", source="fixture", scope="domain", confidence=0.7)
    [item] = memory_entries_to_context_items([entry])
    cw = item.metadata["_contextweaver"]
    assert cw["memory_source"]["id"] == "m1"
    assert cw["memory_source"]["source"] == "fixture"
    assert cw["memory_source"]["scope"] == "domain"
    assert cw["memory_source"]["confidence"] == 0.7


def test_materialise_forwards_tags_to_metadata() -> None:
    entry = _entry("m1", tags=["billing", "p1"])
    [item] = memory_entries_to_context_items([entry])
    assert item.metadata["tags"] == ["billing", "p1"]


def test_materialise_id_prefix() -> None:
    [item] = memory_entries_to_context_items([_entry("user.pref.tone")])
    assert item.id == "memory:user.pref.tone"


# ---------------------------------------------------------------------------
# select_memory_for_phase
# ---------------------------------------------------------------------------


def test_select_for_phase_enforces_token_budget() -> None:
    # Three entries, each ~10 tokens under the default len//4 estimator.
    long_text = "x" * 40
    source = JsonFixtureMemorySource(
        [
            _entry("m1", text=long_text, scope="domain", timestamp=300.0),
            _entry("m2", text=long_text, scope="domain", timestamp=200.0),
            _entry("m3", text=long_text, scope="domain", timestamp=100.0),
        ]
    )
    items = select_memory_for_phase(source, "x", Phase.interpret, budget_tokens=20)
    # Two should fit (10 tokens each); third is skipped.
    assert len(items) == 2
    # Recency tie-break preserved.
    assert [i.id for i in items] == ["memory:m1", "memory:m2"]


def test_select_for_phase_zero_budget_returns_empty() -> None:
    source = JsonFixtureMemorySource([_entry("m1")])
    assert select_memory_for_phase(source, "x", Phase.answer, budget_tokens=0) == []


def test_select_for_phase_skips_oversized_but_packs_smaller_later() -> None:
    # First entry is too big alone; second fits — verifies non-greedy stop.
    source = JsonFixtureMemorySource(
        [
            _entry("big", text="x" * 400, scope="domain", timestamp=300.0),
            _entry("small", text="x" * 20, scope="domain", timestamp=200.0),
        ]
    )
    items = select_memory_for_phase(source, "x", Phase.interpret, budget_tokens=20)
    assert [i.id for i in items] == ["memory:small"]


def test_phase_aware_selection_differs_by_phase() -> None:
    # Pin issue #293's acceptance criterion: same memory pool, different phase
    # → different ordering / selection.
    source = JsonFixtureMemorySource(
        [
            _entry("routing_pref", text="prefer A", scope="routing", timestamp=300.0),
            _entry("domain_fact", text="accounts in db1", scope="domain", timestamp=200.0),
            _entry("call_tip", text="tool needs auth", scope="tool_usage", timestamp=100.0),
        ]
    )
    route = source.select("anything", Phase.route)
    call = source.select("anything", Phase.call)
    interpret = source.select("anything", Phase.interpret)
    assert route[0].id == "routing_pref"
    assert call[0].id == "call_tip"
    assert interpret[0].id == "domain_fact"


# ---------------------------------------------------------------------------
# Sensitivity integration
# ---------------------------------------------------------------------------


def test_sensitivity_filter_drops_memory_above_floor() -> None:
    entries = [
        _entry("public", text="public note", sensitivity=Sensitivity.public),
        _entry("internal", text="internal note", sensitivity=Sensitivity.internal),
        _entry("conf", text="confidential note", sensitivity=Sensitivity.confidential),
        _entry("restr", text="restricted note", sensitivity=Sensitivity.restricted),
    ]
    items = memory_entries_to_context_items(entries)
    # Default policy: floor=confidential, action=drop.
    policy = ContextPolicy()
    filtered, dropped = apply_sensitivity_filter(items, policy)
    kept_ids = [i.id for i in filtered]
    assert "memory:public" in kept_ids
    assert "memory:internal" in kept_ids
    assert "memory:conf" not in kept_ids
    assert "memory:restr" not in kept_ids
    assert dropped == 2


def test_sensitivity_redact_replaces_text_for_memory() -> None:
    items = memory_entries_to_context_items(
        [_entry("secret", text="THE PASSWORD IS 42", sensitivity=Sensitivity.restricted)]
    )
    policy = ContextPolicy(
        sensitivity_action="redact",
        redaction_hooks=["mask"],
    )
    filtered, _ = apply_sensitivity_filter(items, policy)
    assert filtered[0].text == "[REDACTED: restricted]"
    # Raw text MUST NOT survive — invariant from sensitivity rules.
    assert "PASSWORD" not in filtered[0].text


# ---------------------------------------------------------------------------
# Phase scope coverage
# ---------------------------------------------------------------------------


def test_phase_scope_preferences_cover_all_phases() -> None:
    # Pin every phase has at least one preferred scope tag.
    for phase in Phase:
        assert PHASE_SCOPE_PREFERENCES.get(phase), f"missing scope for {phase}"

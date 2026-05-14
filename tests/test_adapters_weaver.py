"""Tests for the weaver-spec contract adapter (issue #143).

Split out from ``tests/test_adapters.py`` so that the module-level
``pytest.importorskip("weaver_contracts")`` skip only suppresses *this* file
when the optional ``weaver_contracts`` package is missing — the MCP / A2A /
FastMCP adapter tests in ``test_adapters.py`` continue to run in minimal
environments (PR #201 review).

The ``weaver_contracts`` package is installed via the ``[dev]`` extras, so
the suite runs unconditionally in CI.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

weaver_contracts = pytest.importorskip("weaver_contracts")

from contextweaver.adapters.weaver_contracts import (  # noqa: E402
    from_weaver_choice_card,
    from_weaver_choice_card_single,
    from_weaver_frame,
    from_weaver_routing_decision,
    from_weaver_selectable_item,
    to_weaver_choice_card,
    to_weaver_choice_cards,
    to_weaver_frame,
    to_weaver_routing_decision,
    to_weaver_selectable_item,
)
from contextweaver.envelope import (  # noqa: E402
    ChoiceCard,
    ResultEnvelope,
    RoutingDecision,
)
from contextweaver.exceptions import CatalogError  # noqa: E402
from contextweaver.types import ArtifactRef, SelectableItem, ViewSpec  # noqa: E402

# ---------------------------------------------------------------------------
# SelectableItem ↔ weaver_contracts.SelectableItem
# ---------------------------------------------------------------------------


def test_to_weaver_selectable_item_basic_fields() -> None:
    item = SelectableItem(
        id="t1", kind="tool", name="search", description="Search the DB", namespace="db"
    )
    spec = to_weaver_selectable_item(item)
    assert isinstance(spec, weaver_contracts.SelectableItem)
    assert spec.id == "t1"
    assert spec.label == "search"
    assert spec.description == "Search the DB"
    assert spec.capability_id == "db:search"


def test_to_weaver_selectable_item_no_namespace_uses_id_as_capability_id() -> None:
    item = SelectableItem(id="t1", kind="tool", name="search", description="d")
    spec = to_weaver_selectable_item(item)
    assert spec.capability_id == "t1"


def test_to_weaver_selectable_item_stashes_cw_extras_in_metadata() -> None:
    item = SelectableItem(
        id="t1",
        kind="agent",
        name="bot",
        description="d",
        tags=["nlp"],
        namespace="ai",
        args_schema={"type": "object"},
        output_schema={"type": "string"},
        examples=["hello"],
        constraints={"max_tokens": 100},
        side_effects=True,
        cost_hint=0.5,
        metadata={"foo": "bar"},
    )
    spec = to_weaver_selectable_item(item)
    assert spec.metadata["foo"] == "bar"
    cw = spec.metadata["_contextweaver"]
    assert cw["kind"] == "agent"
    assert cw["tags"] == ["nlp"]
    assert cw["namespace"] == "ai"
    assert cw["args_schema"] == {"type": "object"}
    assert cw["output_schema"] == {"type": "string"}
    assert cw["examples"] == ["hello"]
    assert cw["constraints"] == {"max_tokens": 100}
    assert cw["side_effects"] is True
    assert cw["cost_hint"] == 0.5


def test_selectable_item_roundtrip_lossless() -> None:
    item = SelectableItem(
        id="t1",
        kind="agent",
        name="bot",
        description="A chatbot",
        tags=["nlp", "ai"],
        namespace="ai",
        args_schema={"type": "object", "properties": {"q": {"type": "string"}}},
        output_schema={"type": "string"},
        examples=["greet"],
        constraints={"max_tokens": 100},
        side_effects=True,
        cost_hint=0.7,
        metadata={"trace_id": "abc"},
    )
    restored = from_weaver_selectable_item(to_weaver_selectable_item(item))
    assert restored == item


def test_selectable_item_roundtrip_defaults() -> None:
    item = SelectableItem(id="t1", kind="tool", name="n", description="d")
    restored = from_weaver_selectable_item(to_weaver_selectable_item(item))
    assert restored == item


def test_from_weaver_selectable_item_foreign_origin() -> None:
    spec = weaver_contracts.SelectableItem(
        id="ext-1",
        label="External tool",
        description="Came from agent-kernel",
        capability_id="agentkernel:do_thing",
        metadata={"external": True},
    )
    item = from_weaver_selectable_item(spec)
    assert item.id == "ext-1"
    assert item.kind == "tool"
    assert item.name == "External tool"
    assert item.namespace == "agentkernel"  # inferred from capability_id
    assert item.metadata == {"external": True}


def test_to_weaver_selectable_item_rejects_reserved_metadata_key() -> None:
    """User metadata using the reserved adapter key must raise (PR #201 review)."""
    item = SelectableItem(
        id="t1",
        kind="tool",
        name="n",
        description="d",
        metadata={"_contextweaver": {"injected": True}},
    )
    with pytest.raises(CatalogError, match="reserved adapter key"):
        to_weaver_selectable_item(item)


# ---------------------------------------------------------------------------
# ChoiceCard ↔ weaver_contracts.ChoiceCard
# ---------------------------------------------------------------------------


def test_to_weaver_choice_card_wraps_single_card_as_menu() -> None:
    card = ChoiceCard(
        id="search",
        name="search",
        description="Search the DB",
        tags=["data"],
        kind="tool",
        namespace="db",
        has_schema=True,
        cost_hint=0.2,
        side_effects=False,
        score=0.95,
    )
    spec = to_weaver_choice_card(card)
    assert isinstance(spec, weaver_contracts.ChoiceCard)
    assert spec.id == "menu:search"
    assert len(spec.items) == 1
    option = spec.items[0]
    assert option.id == "search"
    assert option.label == "search"
    assert option.capability_id == "db:search"


def test_to_weaver_choice_card_custom_menu_id_and_hint() -> None:
    card = ChoiceCard(id="t1", name="n", description="d")
    spec = to_weaver_choice_card(card, menu_id="custom", context_hint="pick one")
    assert spec.id == "custom"
    assert spec.context_hint == "pick one"


def test_choice_card_roundtrip_via_single_helper_lossless() -> None:
    card = ChoiceCard(
        id="search",
        name="search_db",
        description="Search the DB",
        tags=["data", "query"],
        kind="tool",
        namespace="db",
        has_schema=True,
        cost_hint=0.4,
        side_effects=True,
        score=0.83,
    )
    restored = from_weaver_choice_card_single(to_weaver_choice_card(card))
    assert restored == card


def test_choice_card_roundtrip_score_none() -> None:
    card = ChoiceCard(id="t1", name="n", description="d", score=None)
    restored = from_weaver_choice_card_single(to_weaver_choice_card(card))
    assert restored.score is None
    assert restored == card


def test_from_weaver_choice_card_returns_list() -> None:
    cards = [
        ChoiceCard(id="a", name="a", description="A"),
        ChoiceCard(id="b", name="b", description="B"),
        ChoiceCard(id="c", name="c", description="C"),
    ]
    menu = to_weaver_choice_cards(cards, menu_id="menu-1")
    restored = from_weaver_choice_card(menu)
    assert len(restored) == 3
    assert [c.id for c in restored] == ["a", "b", "c"]
    assert restored == cards


def test_to_weaver_choice_cards_empty_raises() -> None:
    with pytest.raises(CatalogError, match="at least one item"):
        to_weaver_choice_cards([], menu_id="m")


def test_from_weaver_choice_card_single_rejects_multi() -> None:
    cards = [ChoiceCard(id="a", name="a", description="A") for _ in range(2)]
    menu = to_weaver_choice_cards(cards, menu_id="m")
    with pytest.raises(CatalogError, match="single-item"):
        from_weaver_choice_card_single(menu)


# ---------------------------------------------------------------------------
# RoutingDecision ↔ weaver_contracts.RoutingDecision
# ---------------------------------------------------------------------------


def test_routing_decision_roundtrip_lossless() -> None:
    cards = [
        ChoiceCard(id="t1", name="search", description="Search", score=0.9),
        ChoiceCard(id="t2", name="filter", description="Filter", score=0.7, tags=["q"]),
    ]
    ts = datetime(2026, 5, 14, 10, 0, 0, tzinfo=timezone.utc)
    rd = RoutingDecision(
        id="rd-1",
        choice_cards=cards,
        timestamp=ts,
        selected_item_id="t1",
        selected_card_id="t1",
        context_summary="searching for reports",
        metadata={"trace_id": "abc"},
    )
    restored = from_weaver_routing_decision(to_weaver_routing_decision(rd))
    assert restored == rd


def test_routing_decision_to_weaver_groups_into_single_menu() -> None:
    cards = [ChoiceCard(id=f"t{i}", name=f"n{i}", description="d") for i in range(3)]
    rd = RoutingDecision(
        id="rd-1",
        choice_cards=cards,
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    spec = to_weaver_routing_decision(rd)
    assert isinstance(spec, weaver_contracts.RoutingDecision)
    assert len(spec.choice_cards) == 1
    assert spec.choice_cards[0].id == "rd-1:menu"
    assert len(spec.choice_cards[0].items) == 3


def test_routing_decision_empty_choice_cards_raises() -> None:
    rd = RoutingDecision(
        id="rd-1",
        choice_cards=[],
        timestamp=datetime.now(timezone.utc),
    )
    with pytest.raises(CatalogError, match="at least one ChoiceCard"):
        to_weaver_routing_decision(rd)


def test_from_weaver_routing_decision_flattens_multiple_menus() -> None:
    # Build a spec decision with TWO menus directly to verify flattening.
    cards_a = [ChoiceCard(id="a1", name="a1", description="A1")]
    cards_b = [
        ChoiceCard(id="b1", name="b1", description="B1"),
        ChoiceCard(id="b2", name="b2", description="B2"),
    ]
    menu_a = to_weaver_choice_cards(cards_a, menu_id="menu-a")
    menu_b = to_weaver_choice_cards(cards_b, menu_id="menu-b")
    spec_rd = weaver_contracts.RoutingDecision(
        id="rd-multi",
        choice_cards=[menu_a, menu_b],
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    rd = from_weaver_routing_decision(spec_rd)
    assert [c.id for c in rd.choice_cards] == ["a1", "b1", "b2"]


def test_to_weaver_routing_decision_validates_against_spec_post_init() -> None:
    # Spec dataclass validates required fields; verify our adapter produces
    # something that passes.
    cards = [ChoiceCard(id="t1", name="n", description="d")]
    rd = RoutingDecision(
        id="rd-1",
        choice_cards=cards,
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    # Should not raise — spec __post_init__ accepts.
    spec = to_weaver_routing_decision(rd)
    assert spec.id == "rd-1"


def test_to_weaver_routing_decision_remaps_selected_card_id_to_menu() -> None:
    """selected_card_id (refers to a CW card) must remap to the spec menu id (PR #201 review)."""
    cards = [
        ChoiceCard(id="t1", name="n1", description="d"),
        ChoiceCard(id="t2", name="n2", description="d"),
    ]
    rd = RoutingDecision(
        id="rd-1",
        choice_cards=cards,
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        selected_item_id="t1",
        selected_card_id="t1",
    )
    spec = to_weaver_routing_decision(rd)
    assert spec.selected_item_id == "t1"
    # The selected_card_id should reference the synthetic menu, not the
    # contextweaver card id which is no longer a card in the spec shape.
    assert spec.selected_card_id == "rd-1:menu"


def test_routing_decision_roundtrip_preserves_selected_card_id() -> None:
    """The remap-and-reverse roundtrip restores the original selected_card_id."""
    cards = [
        ChoiceCard(id="t1", name="n1", description="d"),
        ChoiceCard(id="t2", name="n2", description="d"),
    ]
    rd = RoutingDecision(
        id="rd-1",
        choice_cards=cards,
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
        selected_item_id="t1",
        selected_card_id="t1",
    )
    restored = from_weaver_routing_decision(to_weaver_routing_decision(rd))
    assert restored.selected_card_id == "t1"


# ---------------------------------------------------------------------------
# ResultEnvelope ↔ weaver_contracts.Frame
# ---------------------------------------------------------------------------


def test_to_weaver_frame_basic_fields() -> None:
    env = ResultEnvelope(status="ok", summary="Query returned 5 rows")
    when = datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc)
    frame = to_weaver_frame(env, frame_id="f-1", capability_id="db:search", created_at=when)
    assert isinstance(frame, weaver_contracts.Frame)
    assert frame.frame_id == "f-1"
    assert frame.capability_id == "db:search"
    assert frame.summary == "Query returned 5 rows"
    assert frame.created_at == when


def test_to_weaver_frame_handles_empty_summary() -> None:
    env = ResultEnvelope(status="ok", summary="")
    frame = to_weaver_frame(env, frame_id="f-1", capability_id="cap")
    # Frame.summary post_init rejects empty strings.
    assert frame.summary == "(no summary)"


def test_to_weaver_frame_default_created_at_is_aware() -> None:
    env = ResultEnvelope(status="ok", summary="done")
    frame = to_weaver_frame(env, frame_id="f-1", capability_id="cap")
    assert frame.created_at.tzinfo is not None


def test_to_weaver_frame_handle_refs_from_artifacts() -> None:
    refs = [
        ArtifactRef(handle="h1", media_type="application/json", size_bytes=12, label="a"),
        ArtifactRef(handle="h2", media_type="text/plain", size_bytes=99),
    ]
    env = ResultEnvelope(status="ok", summary="s", artifacts=refs)
    frame = to_weaver_frame(env, frame_id="f-1", capability_id="cap")
    assert frame.handle_refs == ["h1", "h2"]


def test_frame_roundtrip_lossless() -> None:
    refs = [ArtifactRef(handle="h1", media_type="application/json", size_bytes=42, label="r")]
    views = [ViewSpec(view_id="v1", label="rows", selector={"start": 0, "end": 10})]
    env = ResultEnvelope(
        status="partial",
        summary="3/5 rows",
        facts=["count: 3", "status: warning"],
        artifacts=refs,
        views=views,
        provenance={"tool": "db.search", "redaction_notes": "ssn masked"},
    )
    when = datetime(2026, 5, 14, 12, 30, 0, tzinfo=timezone.utc)
    frame = to_weaver_frame(env, frame_id="f-1", capability_id="db:search", created_at=when)
    restored = from_weaver_frame(frame)
    assert restored == env


def test_to_weaver_frame_lifts_redaction_notes_from_provenance() -> None:
    env = ResultEnvelope(
        status="ok",
        summary="s",
        provenance={"redaction_notes": "PII removed from rows 3-5"},
    )
    frame = to_weaver_frame(env, frame_id="f-1", capability_id="cap")
    assert frame.redaction_notes == "PII removed from rows 3-5"


def test_from_weaver_frame_foreign_origin_falls_back_to_defaults() -> None:
    # A Frame produced outside CW (no _contextweaver metadata key).
    when = datetime(2026, 5, 14, tzinfo=timezone.utc)
    frame = weaver_contracts.Frame(
        frame_id="f-ext",
        capability_id="kernel:fetch",
        summary="External summary",
        created_at=when,
        structured_data=None,
        handle_refs=["h-ext-1"],
        redaction_notes="redacted by kernel",
        metadata={"origin": "agent-kernel"},
    )
    env = from_weaver_frame(frame)
    assert env.status == "ok"  # default
    assert env.summary == "External summary"
    assert env.facts == []
    assert env.views == []
    # Stub ArtifactRef constructed from handle_refs.
    assert len(env.artifacts) == 1
    assert env.artifacts[0].handle == "h-ext-1"
    assert env.artifacts[0].media_type == "application/octet-stream"
    assert env.provenance == {"redaction_notes": "redacted by kernel"}


def test_from_weaver_frame_foreign_summary_no_summary_preserved() -> None:
    """Foreign-origin Frame with literal '(no summary)' must keep it verbatim (PR #201 review).

    Only the adapter's own sentinel reversal should trigger when the
    ``_contextweaver`` metadata key proves the Frame came from
    ``to_weaver_frame``; foreign producers might legitimately use that exact
    string.
    """
    when = datetime(2026, 5, 14, tzinfo=timezone.utc)
    frame = weaver_contracts.Frame(
        frame_id="f-ext",
        capability_id="kernel:fetch",
        summary="(no summary)",
        created_at=when,
        metadata={"origin": "agent-kernel"},  # no _contextweaver key
    )
    env = from_weaver_frame(frame)
    assert env.summary == "(no summary)"


def test_from_weaver_frame_invalid_status_defaults_to_ok() -> None:
    when = datetime(2026, 5, 14, tzinfo=timezone.utc)
    frame = weaver_contracts.Frame(
        frame_id="f-1",
        capability_id="cap",
        summary="s",
        created_at=when,
        structured_data={"status": "invalid_value", "facts": [], "views": []},
    )
    env = from_weaver_frame(frame)
    assert env.status == "ok"


# ---------------------------------------------------------------------------
# weaver_contracts adapter — import guard
# ---------------------------------------------------------------------------


def test_weaver_adapter_raises_when_module_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """When weaver_contracts is not importable, public functions raise CatalogError."""
    import sys

    saved = sys.modules.get("weaver_contracts")
    monkeypatch.setitem(sys.modules, "weaver_contracts", None)  # type: ignore[arg-type]

    item = SelectableItem(id="t1", kind="tool", name="n", description="d")
    with pytest.raises(CatalogError, match="weaver_contracts is not installed"):
        to_weaver_selectable_item(item)

    if saved is not None:
        monkeypatch.setitem(sys.modules, "weaver_contracts", saved)
    else:
        monkeypatch.delitem(sys.modules, "weaver_contracts", raising=False)


# ---------------------------------------------------------------------------
# weaver_contracts adapter — preserves unknown spec metadata keys
# ---------------------------------------------------------------------------


def test_to_weaver_selectable_item_does_not_clobber_user_metadata() -> None:
    item = SelectableItem(
        id="t1",
        kind="tool",
        name="n",
        description="d",
        metadata={"user_key": 42, "another": [1, 2]},
    )
    spec = to_weaver_selectable_item(item)
    assert spec.metadata["user_key"] == 42
    assert spec.metadata["another"] == [1, 2]
    assert "_contextweaver" in spec.metadata
    # Round-trip preserves both user and CW metadata.
    restored = from_weaver_selectable_item(spec)
    assert restored.metadata == {"user_key": 42, "another": [1, 2]}

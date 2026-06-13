"""Tests for contextweaver.routing.cards."""

from __future__ import annotations

from contextweaver.envelope import ChoiceCard
from contextweaver.routing.cards import (
    cards_for_route,
    format_card_for_prompt,
    item_to_card,
    make_choice_cards,
    render_cards,
    render_cards_text,
)
from contextweaver.routing.catalog import Catalog
from contextweaver.types import SelectableItem


def _item(
    iid: str,
    description: str = "",
    tags: list[str] | None = None,
    namespace: str = "",
    args_schema: dict | None = None,  # type: ignore[type-arg]
) -> SelectableItem:
    return SelectableItem(
        id=iid,
        kind="tool",
        name=f"tool_{iid}",
        description=description or f"Does {iid}",
        tags=tags or ["test"],
        namespace=namespace,
        args_schema=args_schema or {},
        side_effects=False,
        cost_hint=0.1,
    )


# ------------------------------------------------------------------
# item_to_card
# ------------------------------------------------------------------


def test_item_to_card() -> None:
    item = _item("t1")
    card = item_to_card(item)
    assert card.id == "t1"
    assert card.name == "tool_t1"
    assert card.kind == "tool"
    assert "args_schema" not in card.to_dict()


def test_item_to_card_has_schema_flag() -> None:
    item = _item("t1", args_schema={"type": "object"})
    card = item_to_card(item)
    assert card.has_schema is True


def test_item_to_card_no_schema_flag() -> None:
    item = _item("t1", args_schema={})
    card = item_to_card(item)
    assert card.has_schema is False


def test_item_to_card_preserves_namespace() -> None:
    item = _item("t1", namespace="billing")
    card = item_to_card(item)
    assert card.namespace == "billing"


def test_item_to_card_with_score() -> None:
    item = _item("t1")
    card = item_to_card(item, score=0.85)
    assert card.score == 0.85


# ------------------------------------------------------------------
# render_cards
# ------------------------------------------------------------------


def test_render_cards() -> None:
    items = [_item("t1"), _item("t2")]
    cards = render_cards(items)
    assert len(cards) == 2
    assert cards[0].id == "t1"


# ------------------------------------------------------------------
# make_choice_cards
# ------------------------------------------------------------------


def test_make_choice_cards_default() -> None:
    items = [_item(f"t{i}") for i in range(5)]
    cards = make_choice_cards(items)
    assert len(cards) == 5


def test_make_choice_cards_max_cards() -> None:
    items = [_item(f"t{i}") for i in range(30)]
    cards = make_choice_cards(items, max_cards=10)
    assert len(cards) <= 10


def test_make_choice_cards_preserves_order_no_scores() -> None:
    """Without scores, original input order is preserved when capping."""
    # IDs in reverse alphabetical order
    items = [_item(f"z{i}") for i in range(5)] + [_item(f"a{i}") for i in range(5)]
    cards = make_choice_cards(items, max_cards=5)
    # Should keep first 5 (z0..z4), not alphabetically first
    assert [c.id for c in cards] == [f"z{i}" for i in range(5)]


def test_make_choice_cards_truncates_description_to_token_budget() -> None:
    """Per docs/gateway_spec.md §2.4, descriptions are truncated by tokens."""
    from contextweaver.routing.cards import count_tokens

    long_desc = "A long sentence. " * 100  # ~400 tokens
    items = [_item("t1", description=long_desc)]
    cards = make_choice_cards(items, target_tokens_per_card=60)
    # Truncated to fit the per-card target.
    rendered_line = f"[1/1] {cards[0].id} ({cards[0].kind}) — {cards[0].description}"
    assert count_tokens(rendered_line) <= 80  # hard cap


def test_make_choice_cards_with_scores() -> None:
    items = [_item("t1"), _item("t2")]
    scores = {"t1": 0.9, "t2": 0.3}
    cards = make_choice_cards(items, scores=scores)
    # Should have scores attached
    score_map = {c.id: c.score for c in cards}
    assert score_map["t1"] == 0.9
    assert score_map["t2"] == 0.3


def test_make_choice_cards_score_desc_id_asc_ordering() -> None:
    """§2.5: ties broken by tool_id ascending."""
    items = [_item("t_zzz"), _item("t_aaa"), _item("t_mmm")]
    # All same score → tie-break by id ascending.
    scores = {"t_zzz": 0.5, "t_aaa": 0.5, "t_mmm": 0.5}
    cards = make_choice_cards(items, scores=scores)
    assert [c.id for c in cards] == ["t_aaa", "t_mmm", "t_zzz"]


def test_make_choice_cards_byte_identical_stable_order() -> None:
    """Issue #218: identical inputs produce byte-identical JSON across calls.

    Adopters using Anthropic / OpenAI / Google prompt-caching depend on the
    tool-definition prefix being byte-stable across requests. This regression
    test locks the ``make_choice_cards`` invariant: same input items + same
    scores + same tuning ⇒ same serialised bytes.
    """
    import json

    # Inputs are constructed in a non-trivial order to exercise the sort.
    items = [
        _item("z_tool", description="last alphabetical"),
        _item("a_tool", description="first alphabetical"),
        _item("m_tool", description="middle alphabetical"),
        _item("tied_a", description="tied score, lower id"),
        _item("tied_b", description="tied score, higher id"),
    ]
    scores = {
        "z_tool": 0.7,
        "a_tool": 0.9,
        "m_tool": 0.5,
        "tied_a": 0.3,
        "tied_b": 0.3,
    }

    cards1 = make_choice_cards(items, scores=scores)
    cards2 = make_choice_cards(items, scores=scores)

    # Structural equality of the full list.
    assert [c.to_dict() for c in cards1] == [c.to_dict() for c in cards2]

    # Byte-identical serialisation — what prompt-cache prefix relies on.
    bytes1 = json.dumps([c.to_dict() for c in cards1], sort_keys=True).encode("utf-8")
    bytes2 = json.dumps([c.to_dict() for c in cards2], sort_keys=True).encode("utf-8")
    assert bytes1 == bytes2

    # Expected ordering: score desc, id asc (ties: tied_a before tied_b).
    assert [c.id for c in cards1] == [
        "a_tool",
        "z_tool",
        "m_tool",
        "tied_a",
        "tied_b",
    ]


def test_make_choice_cards_no_schemas_in_output() -> None:
    items = [_item("t1", args_schema={"type": "object", "properties": {"x": {"type": "int"}}})]
    cards = make_choice_cards(items)
    d = cards[0].to_dict()
    assert "args_schema" not in d
    assert d["has_schema"] is True


# ------------------------------------------------------------------
# render_cards_text
# ------------------------------------------------------------------


def test_render_cards_text_format() -> None:
    cards = [
        ChoiceCard(
            id="billing:invoices_search@1.0",
            name="search",
            description="Search invoices by date",
            tags=["billing", "search"],
            kind="tool",
            score=0.82,
        ),
    ]
    text = render_cards_text(cards)
    assert "[1/1]" in text
    assert "billing:invoices_search@1.0" in text
    assert "(tool)" in text
    assert "score=0.82" in text
    assert "[billing, search]" in text


def test_render_cards_text_no_score() -> None:
    cards = [
        ChoiceCard(
            id="t1",
            name="t1",
            description="desc",
            kind="tool",
            score=None,
        ),
    ]
    text = render_cards_text(cards)
    assert "score=" not in text


def test_render_cards_text_numbering() -> None:
    cards = [ChoiceCard(id=f"t{i}", name=f"t{i}", description="d", kind="tool") for i in range(3)]
    text = render_cards_text(cards)
    assert "[1/3]" in text
    assert "[2/3]" in text
    assert "[3/3]" in text


def test_render_cards_text_empty() -> None:
    assert render_cards_text([]) == ""


# ------------------------------------------------------------------
# cards_for_route
# ------------------------------------------------------------------


def test_cards_for_route_skips_non_catalog_nodes() -> None:
    catalog = Catalog()
    catalog.register(_item("t1"))
    route = ["ns:data", "t1", "missing_node"]
    cards = cards_for_route(route, catalog)
    assert len(cards) == 1
    assert cards[0].id == "t1"


# ------------------------------------------------------------------
# format_card_for_prompt
# ------------------------------------------------------------------


def test_format_card_for_prompt() -> None:
    card = ChoiceCard(id="c1", name="search", description="Search records", tags=["search"])
    text = format_card_for_prompt(card)
    assert "c1" in text
    assert "search" in text.lower()


def test_format_card_with_side_effects() -> None:
    card = ChoiceCard(id="c2", name="delete", description="Delete record", side_effects=True)
    text = format_card_for_prompt(card)
    assert "side effects" in text.lower()


# ------------------------------------------------------------------
# ChoiceCard serde with new fields
# ------------------------------------------------------------------


def test_choice_card_roundtrip_new_fields() -> None:
    card = ChoiceCard(
        id="t1",
        name="tool1",
        description="desc",
        kind="agent",
        namespace="billing",
        has_schema=True,
        score=0.75,
    )
    restored = ChoiceCard.from_dict(card.to_dict())
    assert restored.kind == "agent"
    assert restored.namespace == "billing"
    assert restored.has_schema is True
    assert restored.score == 0.75


def test_choice_card_score_none_omitted_from_dict() -> None:
    card = ChoiceCard(id="t1", name="t1", description="d", score=None)
    d = card.to_dict()
    assert "score" not in d


# ------------------------------------------------------------------
# Opt-in secret scrubbing of card text (issue #428)
# ------------------------------------------------------------------


def test_item_to_card_redacts_secret_in_description() -> None:
    secret = "AKIAIOSFODNN7EXAMPLE"
    item = _item("t1", description=f"Use token {secret} to authenticate")
    card = item_to_card(item, redact_secrets=True)
    assert secret not in card.description


def test_item_to_card_redact_off_by_default() -> None:
    secret = "AKIAIOSFODNN7EXAMPLE"
    item = _item("t1", description=f"Use token {secret}")
    card = item_to_card(item)
    assert secret in card.description


def test_make_choice_cards_threads_redaction() -> None:
    secret = "ghp_" + "a" * 36
    items = [_item("t1", description=f"key {secret}")]
    cards = make_choice_cards(items, redact_secrets=True)
    assert secret not in cards[0].description


def test_cards_for_route_threads_redaction() -> None:
    secret = "AKIAIOSFODNN7EXAMPLE"
    catalog = Catalog()
    catalog.register(_item("t1", description=f"key {secret}"))
    cards = cards_for_route(["t1"], catalog, redact_secrets=True)
    assert cards and secret not in cards[0].description


# ------------------------------------------------------------------
# Safety hints: first-class field + capping immunity (issue #516)
# ------------------------------------------------------------------


def test_card_safety_derived_from_destructive_tag() -> None:
    card = item_to_card(_item("t1", tags=["destructive", "write"]))
    assert card.safety == "destructive"


def test_card_safety_derived_from_read_only_tag() -> None:
    assert item_to_card(_item("t1", tags=["read-only"])).safety == "read_only"
    # OpenAPI-adapter underscore spelling is recognised too.
    assert item_to_card(_item("t2", tags=["read_only"])).safety == "read_only"


def test_card_safety_destructive_wins_over_read_only() -> None:
    card = item_to_card(_item("t1", tags=["read-only", "destructive"]))
    assert card.safety == "destructive"


def test_card_safety_unspecified_without_annotation() -> None:
    assert item_to_card(_item("t1", tags=["data", "search"])).safety == ""


def test_destructive_tag_survives_five_tag_cap() -> None:
    # Five tags alphabetically before "destructive" would evict it under the
    # old alphabetical cap; the safety-aware cap must keep it (issue #516).
    card = item_to_card(_item("t1", tags=["aaa", "bbb", "ccc", "ddd", "eee", "destructive"]))
    assert len(card.tags) == 5
    assert "destructive" in card.tags
    assert card.safety == "destructive"


def test_safety_tags_take_priority_in_cap() -> None:
    card = item_to_card(_item("t1", tags=["zzz", "read-only", "yyy", "xxx", "www", "vvv"]))
    assert "read-only" in card.tags
    assert len(card.tags) == 5


def test_card_safety_round_trips() -> None:
    card = item_to_card(_item("t1", tags=["destructive"]))
    assert ChoiceCard.from_dict(card.to_dict()).safety == "destructive"


def test_format_card_marks_destructive() -> None:
    card = item_to_card(_item("t1", tags=["destructive"]))
    assert "destructive" in format_card_for_prompt(card)


def test_format_card_marks_read_only() -> None:
    card = item_to_card(_item("t1", tags=["read-only"]))
    assert "read-only" in format_card_for_prompt(card)


def test_invalid_safety_value_rejected() -> None:
    import pytest

    from contextweaver.exceptions import ValidationError

    with pytest.raises(ValidationError):
        ChoiceCard(id="t1", name="t", description="d", safety="dangerous")  # type: ignore[arg-type]

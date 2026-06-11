"""Non-ASCII regression coverage across the token-sensitive surfaces (issue #525).

Budget enforcement is the product's core guarantee, yet the suite previously had
essentially no CJK / emoji / RTL coverage. These tests pin unicode behaviour
across tokenization, deduplication, card rendering, serialization, and an
in-process context build (the CLI's underlying path), and lock in the
script-aware heuristic so CJK content is no longer under-counted ~4x offline.
"""

from __future__ import annotations

from contextweaver._utils import jaccard, tokenize
from contextweaver.context.manager import ContextManager
from contextweaver.protocols import CharDivFourEstimator, HeuristicEstimator
from contextweaver.routing.cards import (
    count_tokens,
    item_to_card,
    make_choice_cards,
    render_cards_text,
)
from contextweaver.types import ContextItem, ItemKind, Phase, SelectableItem

# Representative fixture corpora across scripts/shapes.
_JA = "これはツールの実行結果です。請求書は未払いのままです。"
_ZH = "这是一个工具调用的结果，发票尚未支付，需要发送提醒。"
_KO = "이것은 도구 실행 결과입니다。미지급 송장에 대한 알림。"
_AR = "هذه نتيجة استدعاء أداة. الفاتورة لا تزال غير مدفوعة."
_EMOJI = "✅ done 🚀🚀 pending ⚠️ 😀"


# ---------------------------------------------------------------------------
# Tokenization / budgeting
# ---------------------------------------------------------------------------


def test_heuristic_does_not_undercount_cjk() -> None:
    """The script-aware heuristic counts CJK ~1 token/char, not ~0.25 (#525)."""
    naive = CharDivFourEstimator()
    smart = HeuristicEstimator()
    for text in (_JA, _ZH, _KO):
        assert smart.estimate(text) >= 3 * naive.estimate(text)
        # ~1 token/char band reflecting documented cl100k CJK behaviour.
        assert 0.6 * len(text) <= smart.estimate(text) <= 1.5 * len(text)


def test_count_tokens_offline_safe_and_positive() -> None:
    """The card token counter (now via tokens.py) handles unicode and never 0."""
    for text in (_JA, _ZH, _KO, _AR, _EMOJI):
        assert count_tokens(text) >= 1
    assert count_tokens("") == 0


def test_cjk_budget_is_enforced_offline() -> None:
    """A CJK transcript must not slip past a phase budget under the heuristic."""
    mgr = ContextManager()  # default HeuristicEstimator
    long_cjk = _ZH * 40  # ~440 dense chars => ~440 heuristic tokens
    mgr.ingest(ContextItem(id="u1", kind=ItemKind.user_turn, text=long_cjk))
    pack = mgr.build_sync(phase=Phase.answer, query="发票", budget_tokens=50)
    assert pack.stats.prompt_tokens <= 50


# ---------------------------------------------------------------------------
# Dedup / similarity on unicode text
# ---------------------------------------------------------------------------


def test_tokenize_and_jaccard_on_unicode() -> None:
    # Tokenization is deterministic on raw unicode (no crash, stable output).
    assert tokenize(_ZH) == tokenize(_ZH)
    assert tokenize(_JA) == tokenize(_JA)
    # Mixed-script content with Latin tokens dedups/compares as expected.
    mixed_a = "invoice 发票 reminder メール"
    mixed_b = "invoice 发票 reminder メール"
    mixed_c = "payment 付款 receipt"
    assert jaccard(tokenize(mixed_a), tokenize(mixed_b)) == 1.0
    assert tokenize(mixed_a)  # Latin tokens survive alongside CJK
    assert jaccard(tokenize(mixed_a), tokenize(mixed_c)) < 1.0


# ---------------------------------------------------------------------------
# Card rendering with emoji tags and RTL descriptions
# ---------------------------------------------------------------------------


def test_card_rendering_with_emoji_tags_and_rtl_description() -> None:
    item = SelectableItem(
        id="billing:reminder",
        kind="tool",
        name="send_reminder",
        description=_AR,  # RTL Arabic description
        tags=["💰", "بريد"],  # emoji + Arabic tag
        namespace="billing",
    )
    card = item_to_card(item, score=0.9)
    assert card.description  # preserved (possibly truncated, never crashes)
    text = render_cards_text([card])
    assert "billing:reminder" in text


def test_make_choice_cards_bounds_hold_for_cjk() -> None:
    items = [
        SelectableItem(
            id=f"ns:{i}",
            kind="tool",
            name=f"tool_{i}",
            description=_ZH * 3,  # long CJK description forces truncation
            namespace="ns",
        )
        for i in range(5)
    ]
    cards = make_choice_cards(items, scores={f"ns:{i}": 1.0 - i / 10 for i in range(5)})
    # Each card stays within the §2.3 hard cap even with dense-script content.
    for card in cards:
        assert count_tokens(render_cards_text([card])) <= 80 + 8


# ---------------------------------------------------------------------------
# Serialization round-trips
# ---------------------------------------------------------------------------


def test_context_item_serde_round_trip_unicode() -> None:
    for text in (_JA, _ZH, _KO, _AR, _EMOJI):
        item = ContextItem(id="x", kind=ItemKind.tool_result, text=text)
        restored = ContextItem.from_dict(item.to_dict())
        assert restored.text == text


def test_build_stamps_unicode_safe_estimator_name() -> None:
    """The build records the estimator path (observability, #493)."""
    mgr = ContextManager()
    mgr.ingest(ContextItem(id="u1", kind=ItemKind.user_turn, text=_JA))
    pack = mgr.build_sync(phase=Phase.answer, query="ツール")
    assert pack.stats.token_estimator == "heuristic/v2"

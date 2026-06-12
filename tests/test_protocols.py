"""Tests for contextweaver.protocols — default implementations."""

from __future__ import annotations

import pytest

from contextweaver.envelope import ContextPack
from contextweaver.protocols import (
    ArtifactStore,
    CharDivFourEstimator,
    EventHook,
    EventLog,
    Extractor,
    HeuristicEstimator,
    Labeler,
    NoOpHook,
    RedactionHook,
    Summarizer,
    TiktokenEstimator,
    TokenEstimator,
)
from contextweaver.types import ContextItem, ItemKind

# ---------------------------------------------------------------------------
# CharDivFourEstimator
# ---------------------------------------------------------------------------


def test_char_div_four_estimator_basic() -> None:
    est = CharDivFourEstimator()
    assert est.estimate("") == 0
    assert est.estimate("abcd") == 1
    assert est.estimate("hello world!") == 3  # 12 // 4


def test_char_div_four_estimator_long_text() -> None:
    est = CharDivFourEstimator()
    text = "a" * 400
    assert est.estimate(text) == 100


def test_char_div_four_satisfies_protocol() -> None:
    est = CharDivFourEstimator()
    assert isinstance(est, TokenEstimator)


def test_char_div_four_has_stable_name() -> None:
    assert CharDivFourEstimator().name == "heuristic/chardiv4"


# ---------------------------------------------------------------------------
# HeuristicEstimator (script-aware, dependency-free) — issue #525
# ---------------------------------------------------------------------------


def test_heuristic_estimator_satisfies_protocol() -> None:
    assert isinstance(HeuristicEstimator(), TokenEstimator)


def test_heuristic_estimator_empty_and_name() -> None:
    est = HeuristicEstimator()
    assert est.estimate("") == 0
    assert est.name == "heuristic/v2"


def test_heuristic_estimator_matches_chardiv_for_ascii() -> None:
    """Latin/ASCII estimates must not change versus the old len // 4 default."""
    est = HeuristicEstimator()
    chardiv = CharDivFourEstimator()
    for text in ["", "abcd", "hello world!", "a" * 400, "The quick brown fox."]:
        assert est.estimate(text) == chardiv.estimate(text)


def test_heuristic_estimator_counts_cjk_near_one_per_char() -> None:
    """CJK/Kana/Hangul count at ~1 token/char, fixing the ~4x under-count (#525).

    External fact: under cl100k-family encodings CJK text runs ~1+ token per
    character. The committed band below encodes that without needing a live
    tiktoken download (offline-deterministic).
    """
    est = HeuristicEstimator()
    for text in ["世界" * 30, "こんにちは" * 12, "안녕하세요" * 12]:
        nchars = len(text)
        est_tokens = est.estimate(text)
        # ~1 token/char band (the documented CJK ratio) ...
        assert 0.7 * nchars <= est_tokens <= 1.5 * nchars
        # ... and a large improvement over the ~0.25/char naive heuristic.
        assert est_tokens >= 3 * CharDivFourEstimator().estimate(text)


def test_heuristic_estimator_handles_emoji_and_mixed_script() -> None:
    est = HeuristicEstimator()
    # Emoji count as wide characters (≈1 token) rather than ~0.25.
    assert est.estimate("😀😀😀😀") >= 4
    # Mixed Latin + CJK: Latin at len//4, CJK at ~1/char.
    mixed = "hello " + "世界"  # 6 narrow chars -> 1, 2 wide -> 2
    assert est.estimate(mixed) == len("hello ") // 4 + 2


# ---------------------------------------------------------------------------
# TiktokenEstimator (tiktoken is a core dep)
# ---------------------------------------------------------------------------


def test_tiktoken_estimator_basic() -> None:
    est = TiktokenEstimator()
    result = est.estimate("hello world")
    assert isinstance(result, int)
    assert result > 0


def test_tiktoken_estimator_custom_model() -> None:
    est = TiktokenEstimator(model="cl100k_base")
    result = est.estimate("test")
    assert isinstance(result, int)


def test_tiktoken_estimator_accepts_model_name() -> None:
    """model param should accept a model name like 'gpt-4', not just encoding names."""
    est = TiktokenEstimator(model="gpt-4")
    result = est.estimate("hello")
    assert isinstance(result, int)
    assert result > 0


def test_tiktoken_more_accurate_than_chardiv() -> None:
    """tiktoken should produce different counts than the char/4 heuristic on real text.

    Only meaningful when tiktoken's BPE encoding is actually available
    (i.e., not falling back to the heuristic due to offline / cache miss).
    Skipped in offline environments where tiktoken cannot download encodings.
    """
    est = TiktokenEstimator()
    if est._fallback is not None:
        pytest.skip("tiktoken encoding not available offline; fallback active")
    text = "The quick brown fox jumps over the lazy dog." * 10
    tt = est.estimate(text)
    cd = CharDivFourEstimator().estimate(text)
    assert tt > 0
    assert cd > 0
    # Real tokenization differs from chars // 4 for non-trivial text.
    assert tt != cd


def test_tiktoken_fallback_when_encoding_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When tiktoken can't load an encoding, estimator falls back transparently."""
    import contextweaver.protocols as protocols_mod

    def _raise(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("simulated download failure")

    monkeypatch.setattr(protocols_mod._tiktoken, "encoding_for_model", _raise)
    monkeypatch.setattr(protocols_mod._tiktoken, "get_encoding", _raise)

    est = TiktokenEstimator()
    assert est._fallback is not None
    # The estimator-path name reflects the heuristic fallback (issue #493).
    assert est.name == "heuristic/v2"
    # Falls back to the script-aware heuristic; ASCII stays len // 4.
    text = "abcd" * 10  # 40 chars -> 10 tokens via char/4
    assert est.estimate(text) == 10


# ---------------------------------------------------------------------------
# NoOpHook
# ---------------------------------------------------------------------------


def test_noop_hook_on_context_built() -> None:
    hook = NoOpHook()
    pack = ContextPack(prompt="test")
    hook.on_context_built(pack)  # should not raise


def test_noop_hook_on_firewall_triggered() -> None:
    hook = NoOpHook()
    item = ContextItem(id="i1", kind=ItemKind.tool_result, text="raw")
    hook.on_firewall_triggered(item, "too long")  # should not raise


def test_noop_hook_on_items_excluded() -> None:
    hook = NoOpHook()
    items = [ContextItem(id="i1", kind=ItemKind.user_turn, text="hi")]
    hook.on_items_excluded(items, "budget")  # should not raise


def test_noop_hook_on_budget_exceeded() -> None:
    hook = NoOpHook()
    hook.on_budget_exceeded(5000, 3000)  # should not raise


def test_noop_hook_on_route_completed() -> None:
    hook = NoOpHook()
    hook.on_route_completed(["tool_a", "tool_b"])  # should not raise


def test_noop_hook_satisfies_protocol() -> None:
    hook = NoOpHook()
    assert isinstance(hook, EventHook)


# ---------------------------------------------------------------------------
# Protocol runtime checks
# ---------------------------------------------------------------------------


def test_token_estimator_is_runtime_checkable() -> None:
    assert isinstance(CharDivFourEstimator(), TokenEstimator)


def test_event_hook_is_runtime_checkable() -> None:
    assert isinstance(NoOpHook(), EventHook)


def test_summarizer_is_runtime_checkable() -> None:
    class _S:
        def summarize(self, raw: str, metadata: dict) -> str:  # type: ignore[type-arg]
            return raw[:50]

    assert isinstance(_S(), Summarizer)


def test_extractor_is_runtime_checkable() -> None:
    class _E:
        def extract(self, raw: str, metadata: dict) -> list[str]:  # type: ignore[type-arg]
            return []

    assert isinstance(_E(), Extractor)


def test_redaction_hook_is_runtime_checkable() -> None:
    class _R:
        def redact(self, item: ContextItem) -> ContextItem:
            return item

    assert isinstance(_R(), RedactionHook)


def test_labeler_is_runtime_checkable() -> None:
    class _L:
        def label(self, item: object) -> tuple[str, str]:
            return ("misc", "low")

    assert isinstance(_L(), Labeler)


def test_event_log_is_runtime_checkable() -> None:
    from contextweaver.store.event_log import InMemoryEventLog

    log = InMemoryEventLog()
    assert isinstance(log, EventLog)


def test_artifact_store_is_runtime_checkable() -> None:
    from contextweaver.store.artifacts import InMemoryArtifactStore

    store = InMemoryArtifactStore()
    assert isinstance(store, ArtifactStore)


def test_episodic_store_is_runtime_checkable() -> None:
    from contextweaver.protocols import EpisodicStore
    from contextweaver.store.episodic import InMemoryEpisodicStore

    store = InMemoryEpisodicStore()
    assert isinstance(store, EpisodicStore)


def test_fact_store_is_runtime_checkable() -> None:
    from contextweaver.protocols import FactStore
    from contextweaver.store.facts import InMemoryFactStore

    store = InMemoryFactStore()
    assert isinstance(store, FactStore)

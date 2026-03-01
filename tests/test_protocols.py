"""Tests for contextweaver.protocols — default implementations."""

from __future__ import annotations

from contextweaver.envelope import ContextPack
from contextweaver.protocols import (
    ArtifactStore,
    CharDivFourEstimator,
    EventHook,
    EventLog,
    Extractor,
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


# ---------------------------------------------------------------------------
# TiktokenEstimator (stub fallback)
# ---------------------------------------------------------------------------


def test_tiktoken_estimator_fallback() -> None:
    est = TiktokenEstimator()
    # Should work regardless of whether tiktoken is installed
    result = est.estimate("hello world")
    assert isinstance(result, int)
    assert result > 0


def test_tiktoken_estimator_custom_model() -> None:
    est = TiktokenEstimator(model="cl100k_base")
    result = est.estimate("test")
    assert isinstance(result, int)


def test_tiktoken_estimator_accepts_model_name() -> None:
    """model param should accept a model name like 'gpt-4', not just encoding names."""
    # Whether tiktoken is installed or not, this must not raise.
    est = TiktokenEstimator(model="gpt-4")
    result = est.estimate("hello")
    assert isinstance(result, int)
    assert result > 0


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

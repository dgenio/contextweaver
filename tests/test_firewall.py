"""Tests for contextweaver.context.firewall."""

from __future__ import annotations

from unittest.mock import patch

from contextweaver.context.firewall import apply_firewall, apply_firewall_to_batch
from contextweaver.store.artifacts import InMemoryArtifactStore
from contextweaver.types import ContextItem, ItemKind


def test_non_tool_result_passthrough() -> None:
    item = ContextItem(id="u1", kind=ItemKind.user_turn, text="hello")
    store = InMemoryArtifactStore()
    processed, env = apply_firewall(item, store)
    assert processed is item
    assert env is None
    assert len(store.list_refs()) == 0


def test_tool_result_intercepted() -> None:
    item = ContextItem(
        id="r1", kind=ItemKind.tool_result, text="status: ok\nresult: 42 rows\n- row1\n- row2"
    )
    store = InMemoryArtifactStore()
    processed, env = apply_firewall(item, store)
    assert env is not None
    assert env.status == "ok"
    # Raw content stored in artifact store
    assert store.get(f"artifact:{item.id}") is not None
    # Processed item has shorter text (summary)
    assert len(processed.text) <= len(item.text)
    assert processed.artifact_ref is not None


def test_firewall_skips_envelope_ingested_items() -> None:
    # Issue #352: items ingested via the canonical ingest_envelope seam are
    # already firewalled upstream. The build-time firewall must not re-fire on
    # them even when the upstream ArtifactRef carries no content_hash (the
    # foreign-Frame case), or it would clobber the upstream handle.
    from contextweaver.types import ArtifactRef

    upstream_ref = ArtifactRef(
        handle="upstream:frame-1",
        media_type="application/octet-stream",
        size_bytes=0,
        label="upstream handle",
    )
    item = ContextItem(
        id="result:tc1",
        kind=ItemKind.tool_result,
        text="upstream summary",
        metadata={"ingest": "envelope"},
        artifact_ref=upstream_ref,
    )
    store = InMemoryArtifactStore()
    processed, env = apply_firewall(item, store)
    assert processed is item
    assert env is None
    # No spurious re-firewall artifact created; upstream handle preserved.
    assert len(store.list_refs()) == 0
    assert processed.artifact_ref is upstream_ref
    assert processed.text == "upstream summary"


def test_firewall_extracts_facts() -> None:
    item = ContextItem(
        id="r2", kind=ItemKind.tool_result, text="status: ok\ncount: 5\n1. first\n2. second"
    )
    store = InMemoryArtifactStore()
    _, env = apply_firewall(item, store)
    assert env is not None
    assert len(env.facts) >= 1


def test_apply_firewall_to_batch() -> None:
    items = [
        ContextItem(id="u1", kind=ItemKind.user_turn, text="hello"),
        ContextItem(id="r1", kind=ItemKind.tool_result, text="raw output here"),
        ContextItem(id="a1", kind=ItemKind.agent_msg, text="agent response"),
    ]
    store = InMemoryArtifactStore()
    processed, envelopes = apply_firewall_to_batch(items, store)
    assert len(processed) == 3
    assert len(envelopes) == 1
    assert envelopes[0].provenance["source_item_id"] == "r1"


def test_firewall_error_status_when_summary_fails() -> None:
    item = ContextItem(id="r3", kind=ItemKind.tool_result, text="some output")
    store = InMemoryArtifactStore()
    with patch(
        "contextweaver.context.firewall._default_summary",
        side_effect=ValueError("boom"),
    ):
        _, env = apply_firewall(item, store)
    assert env is not None
    assert env.status == "error"
    assert env.summary == "(summary unavailable)"


def test_firewall_partial_status_when_extraction_fails() -> None:
    item = ContextItem(id="r4", kind=ItemKind.tool_result, text="some output")
    store = InMemoryArtifactStore()
    with patch(
        "contextweaver.context.firewall.extract_facts",
        side_effect=ValueError("boom"),
    ):
        _, env = apply_firewall(item, store)
    assert env is not None
    assert env.status == "partial"
    assert env.facts == []


def test_firewall_propagates_media_type_from_metadata() -> None:
    item = ContextItem(
        id="r5",
        kind=ItemKind.tool_result,
        text='{"key": "value"}',
        metadata={"media_type": "application/json"},
    )
    store = InMemoryArtifactStore()
    processed, env = apply_firewall(item, store)
    assert env is not None
    assert processed.artifact_ref is not None
    assert processed.artifact_ref.media_type == "application/json"


def test_firewall_defaults_media_type_to_text_plain() -> None:
    item = ContextItem(
        id="r6",
        kind=ItemKind.tool_result,
        text="plain text output",
    )
    store = InMemoryArtifactStore()
    processed, env = apply_firewall(item, store)
    assert env is not None
    assert processed.artifact_ref is not None
    assert processed.artifact_ref.media_type == "text/plain"


def test_custom_summarizer_is_used() -> None:
    """When a Summarizer is provided it replaces the built-in heuristic."""

    class UpperSummarizer:
        def summarize(self, raw: str, metadata: dict) -> str:
            return raw.upper()

    item = ContextItem(id="r7", kind=ItemKind.tool_result, text="hello world")
    store = InMemoryArtifactStore()
    _, env = apply_firewall(item, store, summarizer=UpperSummarizer())
    assert env is not None
    assert env.summary == "HELLO WORLD"


def test_custom_extractor_is_used() -> None:
    """When an Extractor is provided it replaces the built-in extract_facts."""

    class ConstExtractor:
        def extract(self, raw: str, metadata: dict) -> list[str]:
            return ["custom-fact"]

    item = ContextItem(id="r8", kind=ItemKind.tool_result, text="any text")
    store = InMemoryArtifactStore()
    _, env = apply_firewall(item, store, extractor=ConstExtractor())
    assert env is not None
    assert env.facts == ["custom-fact"]


def test_batch_passes_summarizer_and_extractor() -> None:
    """apply_firewall_to_batch forwards summarizer/extractor to each call."""

    class TagSummarizer:
        def summarize(self, raw: str, metadata: dict) -> str:
            return f"[summary]{raw}"

    class TagExtractor:
        def extract(self, raw: str, metadata: dict) -> list[str]:
            return [f"[fact]{raw}"]

    items = [ContextItem(id="r9", kind=ItemKind.tool_result, text="data")]
    store = InMemoryArtifactStore()
    processed, envelopes = apply_firewall_to_batch(
        items,
        store,
        summarizer=TagSummarizer(),
        extractor=TagExtractor(),
    )
    assert envelopes[0].summary == "[summary]data"
    assert envelopes[0].facts == ["[fact]data"]


def test_custom_summarizer_error_falls_back() -> None:
    """When a custom Summarizer raises, status is 'error' and fallback summary is used."""

    class BrokenSummarizer:
        def summarize(self, raw: str, metadata: dict) -> str:
            raise ValueError("boom")

    item = ContextItem(id="r10", kind=ItemKind.tool_result, text="some output")
    store = InMemoryArtifactStore()
    _, env = apply_firewall(item, store, summarizer=BrokenSummarizer())
    assert env is not None
    assert env.status == "error"
    assert env.summary == "(summary unavailable)"


def test_custom_extractor_error_falls_back() -> None:
    """When a custom Extractor raises, status is 'partial' and facts fall back to []."""

    class BrokenExtractor:
        def extract(self, raw: str, metadata: dict) -> list[str]:
            raise ValueError("boom")

    item = ContextItem(id="r11", kind=ItemKind.tool_result, text="some output")
    store = InMemoryArtifactStore()
    _, env = apply_firewall(item, store, extractor=BrokenExtractor())
    assert env is not None
    assert env.status == "partial"
    assert env.facts == []


# ---------------------------------------------------------------------------
# Issue #190 — content-addressed idempotency
# ---------------------------------------------------------------------------


def test_apply_firewall_sets_content_hash_on_returned_ref() -> None:
    """The artifact ref returned from the first firewall pass carries a sha256 hash."""
    import hashlib

    raw = "raw tool output that should be stored verbatim"
    item = ContextItem(id="r-hash", kind=ItemKind.tool_result, text=raw)
    store = InMemoryArtifactStore()
    processed, env = apply_firewall(item, store)
    expected = hashlib.sha256(raw.encode("utf-8")).hexdigest()
    assert processed.artifact_ref is not None
    assert processed.artifact_ref.content_hash == expected


def test_apply_firewall_is_idempotent_on_already_processed_items() -> None:
    """Issue #190: re-running the firewall must not overwrite raw bytes.

    The bug: ``apply_firewall`` would re-fire on items whose ``text``
    had already been replaced with the summary, storing the summary
    under the same handle and destroying the original raw payload.
    """
    raw = "x" * 10_000
    item = ContextItem(id="r190", kind=ItemKind.tool_result, text=raw)
    store = InMemoryArtifactStore()

    # First pass — fires.
    processed_a, env_a = apply_firewall(item, store)
    assert env_a is not None
    assert processed_a.artifact_ref is not None
    handle = processed_a.artifact_ref.handle
    raw_after_first = store.get(handle)

    # Second pass on the post-firewall item — must be a no-op.
    processed_b, env_b = apply_firewall(processed_a, store)
    assert env_b is None  # nothing to compact a second time
    raw_after_second = store.get(handle)

    # Raw bytes preserved across the second pass.
    assert raw_after_first == raw_after_second
    assert raw_after_first.decode("utf-8") == raw

"""Tests for contextweaver.context.firewall -- large/small outputs, structured extraction, apply_firewall."""

from __future__ import annotations

from contextweaver.context.firewall import apply_firewall
from contextweaver.protocols import CharDivFourEstimator
from contextweaver.store.artifacts import InMemoryArtifactStore
from contextweaver.summarize.extract import StructuredExtractor
from contextweaver.summarize.rules import RuleBasedSummarizer
from contextweaver.types import ItemKind


class TestApplyFirewall:
    """Tests for the apply_firewall async function."""

    async def test_small_output_passthrough(self) -> None:
        store = InMemoryArtifactStore()
        item, envelope = await apply_firewall(
            raw_output="status: ok\nresult: 42 rows",
            tool_call_id="tc1",
            tool_name="db_query",
            media_type="text/plain",
            artifact_store=store,
            summarizer=RuleBasedSummarizer(),
            extractor=StructuredExtractor(),
            token_estimator=CharDivFourEstimator(),
            firewall_threshold=2000,
        )
        assert item.kind == ItemKind.TOOL_RESULT
        assert "42 rows" in item.text
        assert item.artifact_ref is None
        assert len(store.list_refs()) == 0
        assert envelope.status == "ok"

    async def test_large_output_intercepted(self) -> None:
        store = InMemoryArtifactStore()
        large_text = "data line\n" * 500
        item, envelope = await apply_firewall(
            raw_output=large_text,
            tool_call_id="tc2",
            tool_name="big_query",
            media_type="text/plain",
            artifact_store=store,
            summarizer=RuleBasedSummarizer(),
            extractor=StructuredExtractor(),
            token_estimator=CharDivFourEstimator(),
            firewall_threshold=2000,
        )
        assert item.artifact_ref is not None
        assert len(item.text) < len(large_text)
        assert len(store.list_refs()) == 1
        assert envelope.status == "ok"
        assert len(envelope.artifacts) == 1
        assert envelope.artifacts[0].handle == item.artifact_ref

    async def test_large_output_stores_metadata(self) -> None:
        store = InMemoryArtifactStore()
        item, envelope = await apply_firewall(
            raw_output="Y" * 3000,
            tool_call_id="tc3",
            tool_name="meta_tool",
            media_type="application/json",
            artifact_store=store,
            summarizer=RuleBasedSummarizer(),
            extractor=StructuredExtractor(),
            token_estimator=CharDivFourEstimator(),
            firewall_threshold=2000,
        )
        meta = await store.metadata(item.artifact_ref)
        assert meta["tool_name"] == "meta_tool"
        assert meta["media_type"] == "application/json"

    async def test_structured_extraction_facts(self) -> None:
        store = InMemoryArtifactStore()
        item, envelope = await apply_firewall(
            raw_output='{"name": "Alice", "count": 42}',
            tool_call_id="tc4",
            tool_name="json_tool",
            media_type="application/json",
            artifact_store=store,
            summarizer=RuleBasedSummarizer(),
            extractor=StructuredExtractor(),
            token_estimator=CharDivFourEstimator(),
            firewall_threshold=2000,
        )
        assert envelope.facts["type"] == "json_object"
        assert "name" in envelope.facts.get("keys", [])

    async def test_firewall_parent_id_set(self) -> None:
        store = InMemoryArtifactStore()
        item, _ = await apply_firewall(
            raw_output="some output",
            tool_call_id="tc_parent",
            tool_name="tool",
            media_type="text/plain",
            artifact_store=store,
            summarizer=RuleBasedSummarizer(),
            extractor=StructuredExtractor(),
            token_estimator=CharDivFourEstimator(),
            firewall_threshold=2000,
        )
        assert item.parent_id == "tc_parent"

    async def test_bytes_input(self) -> None:
        store = InMemoryArtifactStore()
        item, envelope = await apply_firewall(
            raw_output=b"binary content here",
            tool_call_id="tc5",
            tool_name="binary_tool",
            media_type="application/octet-stream",
            artifact_store=store,
            summarizer=RuleBasedSummarizer(),
            extractor=StructuredExtractor(),
            token_estimator=CharDivFourEstimator(),
            firewall_threshold=2000,
        )
        assert item.text == "binary content here"
        assert envelope.status == "ok"

    async def test_views_created_for_large_output(self) -> None:
        store = InMemoryArtifactStore()
        item, envelope = await apply_firewall(
            raw_output="Z" * 5000,
            tool_call_id="tc6",
            tool_name="views_tool",
            media_type="text/plain",
            artifact_store=store,
            summarizer=RuleBasedSummarizer(),
            extractor=StructuredExtractor(),
            token_estimator=CharDivFourEstimator(),
            firewall_threshold=2000,
        )
        assert len(envelope.views) >= 1
        assert envelope.views[0].artifact_ref == item.artifact_ref

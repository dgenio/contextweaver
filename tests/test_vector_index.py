"""Tests for contextweaver.routing.vector_index (issue #387).

Uses the stdlib-only deterministic
:class:`~contextweaver.extras.embeddings_hashing.HashingEmbeddingBackend`
(never downloads a model).
"""

from __future__ import annotations

import pytest

from contextweaver.exceptions import ConfigError
from contextweaver.extras.embeddings_hashing import HashingEmbeddingBackend
from contextweaver.routing.vector_index import SECTION_ORDER, VectorIndex, canonical_tool_text
from contextweaver.types import SelectableItem


class CountingBackend:
    """Wraps HashingEmbeddingBackend, recording every embed() batch."""

    def __init__(self) -> None:
        self._inner = HashingEmbeddingBackend()
        self.embed_calls: list[list[str]] = []

    def embed(self, texts: list[str]) -> list[list[float]]:
        self.embed_calls.append(list(texts))
        return self._inner.embed(texts)

    def similarity(
        self,
        query_vec: list[float],
        corpus_vecs: list[list[float]],
    ) -> list[float]:
        return self._inner.similarity(query_vec, corpus_vecs)

    @property
    def embedded_text_count(self) -> int:
        return sum(len(batch) for batch in self.embed_calls)


def _item(
    iid: str,
    *,
    name: str | None = None,
    description: str = "generic description",
    tags: list[str] | None = None,
    namespace: str = "",
    args_schema: dict | None = None,
    output_schema: dict | None = None,
    examples: list[str] | None = None,
    metadata: dict | None = None,
) -> SelectableItem:
    return SelectableItem(
        id=iid,
        kind="tool",
        name=name if name is not None else iid,
        description=description,
        tags=tags or [],
        namespace=namespace,
        args_schema=args_schema or {},
        output_schema=output_schema,
        examples=examples or [],
        metadata=metadata or {},
    )


def _catalog() -> list[SelectableItem]:
    return [
        _item(
            "weather.forecast",
            name="get_weather_forecast",
            description="Fetch the weather forecast for a city",
            tags=["weather", "forecast"],
            namespace="weather",
        ),
        _item(
            "billing.invoices",
            name="search_invoices",
            description="Search customer invoices by amount and date",
            tags=["billing", "invoices"],
            namespace="billing",
        ),
        _item(
            "email.send",
            name="send_email",
            description="Send an email message to a recipient",
            tags=["email"],
            namespace="comms",
        ),
    ]


# ---------------------------------------------------------------------------
# canonical_tool_text
# ---------------------------------------------------------------------------


class TestCanonicalToolText:
    def test_labeled_sections_in_stable_order(self) -> None:
        item = _item(
            "t1",
            name="get_weather",
            description="Fetch weather",
            tags=["zeta", "alpha"],
            namespace="wx",
            args_schema={
                "properties": {
                    "units": {"description": "Celsius or Fahrenheit"},
                    "city": {"description": "City name"},
                }
            },
            output_schema={"properties": {"wind": {}, "temperature": {}}},
            examples=["what is the weather", "forecast for Lisbon"],
            metadata={
                "_contextweaver": {
                    "inventory": {"owner": "platform-team", "domain": "weather", "lifecycle": "ga"}
                }
            },
        )
        text = canonical_tool_text(item)
        lines = text.splitlines()
        assert [line.split(":", 1)[0] for line in lines] == list(SECTION_ORDER)
        assert lines[0] == "name: get_weather"
        assert lines[1] == "description: Fetch weather"
        # args properties sorted by name, with descriptions; output props sorted.
        assert lines[2] == (
            "schema: city: City name; units: Celsius or Fahrenheit; output: temperature, wind"
        )
        # examples sorted, tags sorted, namespace, then inventory metadata.
        assert lines[3] == (
            "metadata: examples: forecast for Lisbon; what is the weather; "
            "tags: alpha, zeta; namespace: wx; "
            "owner: platform-team; domain: weather; lifecycle: ga"
        )

    def test_deterministic_for_identical_items(self) -> None:
        catalog = _catalog()
        assert canonical_tool_text(catalog[0]) == canonical_tool_text(_catalog()[0])

    def test_defensive_against_malformed_metadata(self) -> None:
        # Non-dict inventory / _contextweaver blocks must not raise.
        item = _item("t1", metadata={"_contextweaver": "not-a-dict"})
        assert "metadata:" in canonical_tool_text(item)
        item2 = _item("t2", metadata={"_contextweaver": {"inventory": ["not", "a", "dict"]}})
        assert "metadata:" in canonical_tool_text(item2)
        item3 = _item("t3", metadata={"_contextweaver": {"inventory": {"owner": 42}}})
        assert "owner" not in canonical_tool_text(item3)

    def test_defensive_against_malformed_schema(self) -> None:
        item = _item("t1", args_schema={"properties": "not-a-dict"})
        assert canonical_tool_text(item).splitlines()[2] == "schema: "
        item2 = _item("t2", args_schema={"properties": {"q": "not-a-dict"}})
        assert "q" in canonical_tool_text(item2)


# ---------------------------------------------------------------------------
# build + query
# ---------------------------------------------------------------------------


class TestBuildAndQuery:
    def test_distinctive_query_ranks_relevant_tool_first(self) -> None:
        index = VectorIndex(HashingEmbeddingBackend())
        index.build(_catalog())
        assert index.query("weather forecast for a city", top_k=3)[0][0] == "weather.forecast"
        assert index.query("search customer invoices", top_k=3)[0][0] == "billing.invoices"
        assert index.query("send an email message", top_k=3)[0][0] == "email.send"

    def test_evidence_labels(self) -> None:
        index = VectorIndex(HashingEmbeddingBackend())
        index.build(_catalog())
        # "forecast" appears in both name and description → name wins the
        # section-order tie-break only if overlap is strictly greater; here
        # name tokens {get_weather_forecast, get, ...} — check description hit.
        results = {iid: evidence for iid, _, evidence in index.query("invoices", top_k=3)}
        assert results["billing.invoices"] in {"name", "description"}
        # No token overlap anywhere → semantic.
        results = index.query("zzzqqq", top_k=3)
        assert all(evidence == "semantic" for _, _, evidence in results)

    def test_evidence_prefers_earlier_section_on_ties(self) -> None:
        items = [
            _item("a", name="alpha", description="alpha", tags=["alpha"]),
        ]
        index = VectorIndex(HashingEmbeddingBackend())
        index.build(items)
        # "alpha" overlaps name, description, and metadata equally (1 token);
        # section order makes "name" the deterministic winner.
        assert index.query("alpha", top_k=1)[0][2] == "name"

    def test_deterministic_ordering_and_input_order_independence(self) -> None:
        catalog = _catalog()
        index_a = VectorIndex(HashingEmbeddingBackend())
        index_a.build(catalog)
        index_b = VectorIndex(HashingEmbeddingBackend())
        index_b.build(list(reversed(catalog)))
        assert index_a.item_ids == sorted(it.id for it in catalog)
        assert index_a.item_ids == index_b.item_ids
        query = "weather forecast"
        assert index_a.query(query, top_k=3) == index_b.query(query, top_k=3)
        assert index_a.query(query, top_k=3) == index_a.query(query, top_k=3)

    def test_top_k_truncation(self) -> None:
        index = VectorIndex(HashingEmbeddingBackend())
        index.build(_catalog())
        assert len(index.query("weather", top_k=2)) == 2
        assert len(index.query("weather", top_k=99)) == 3

    def test_duplicate_ids_rejected(self) -> None:
        index = VectorIndex(HashingEmbeddingBackend())
        with pytest.raises(ConfigError):
            index.build([_item("dup"), _item("dup")])


# ---------------------------------------------------------------------------
# refresh
# ---------------------------------------------------------------------------


class TestRefresh:
    def test_refresh_reembeds_only_changed_items(self) -> None:
        backend = CountingBackend()
        index = VectorIndex(backend)
        catalog = _catalog()
        index.build(catalog)
        assert backend.embedded_text_count == 3

        changed = _catalog()
        changed[1] = _item(
            "billing.invoices",
            name="search_invoices",
            description="Completely new invoice search description",
            tags=["billing", "invoices"],
            namespace="billing",
        )
        n_changed = index.refresh(changed)
        assert n_changed == 1
        # Only the changed item's canonical text was embedded again.
        assert backend.embedded_text_count == 4
        assert backend.embed_calls[-1] == [canonical_tool_text(changed[1])]

    def test_refresh_noop_when_nothing_changed(self) -> None:
        backend = CountingBackend()
        index = VectorIndex(backend)
        index.build(_catalog())
        before = index.query("weather forecast", top_k=3)
        item_texts_embedded = backend.embedded_text_count  # 3 items + 1 query
        assert index.refresh(_catalog()) == 0
        assert backend.embedded_text_count == item_texts_embedded  # nothing re-embedded
        assert index.query("weather forecast", top_k=3) == before

    def test_refresh_adds_and_removes_items(self) -> None:
        backend = CountingBackend()
        index = VectorIndex(backend)
        index.build(_catalog())
        new_catalog = _catalog()[:2] + [_item("crm.leads", description="Manage sales leads")]
        assert index.refresh(new_catalog) == 1  # only the new item embeds
        assert index.item_ids == ["billing.invoices", "crm.leads", "weather.forecast"]

    def test_refresh_on_empty_index_builds_everything(self) -> None:
        backend = CountingBackend()
        index = VectorIndex(backend)
        assert index.refresh(_catalog()) == 3


# ---------------------------------------------------------------------------
# duplicates
# ---------------------------------------------------------------------------


class TestDuplicates:
    def test_finds_same_description_pair_and_not_unrelated(self) -> None:
        twin_a = _item(
            "srv1.lookup_user",
            name="lookup_user",
            description="Look up a user account by email address",
            tags=["users"],
        )
        twin_b = _item(
            "srv2.lookup_user",
            name="lookup_user",
            description="Look up a user account by email address",
            tags=["users"],
        )
        unrelated = _item(
            "weather.forecast",
            name="get_weather_forecast",
            description="Fetch the weather forecast for a city",
        )
        index = VectorIndex(HashingEmbeddingBackend())
        index.build([unrelated, twin_b, twin_a])
        pairs = index.duplicates(threshold=0.92)
        assert [(a, b) for a, b, _ in pairs] == [("srv1.lookup_user", "srv2.lookup_user")]
        assert pairs[0][2] >= 0.92

    def test_threshold_validation(self) -> None:
        index = VectorIndex(HashingEmbeddingBackend())
        index.build(_catalog())
        with pytest.raises(ConfigError):
            index.duplicates(threshold=1.5)
        with pytest.raises(ConfigError):
            index.duplicates(threshold=-0.1)


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_empty_catalog(self) -> None:
        index = VectorIndex(HashingEmbeddingBackend())
        index.build([])
        assert index.query("anything", top_k=5) == []
        assert index.duplicates() == []
        assert index.item_ids == []

    def test_unbuilt_index_behaves_as_empty(self) -> None:
        index = VectorIndex(HashingEmbeddingBackend())
        assert index.query("anything", top_k=5) == []
        assert index.duplicates() == []

    def test_blank_query_and_nonpositive_top_k(self) -> None:
        index = VectorIndex(HashingEmbeddingBackend())
        index.build(_catalog())
        assert index.query("", top_k=5) == []
        assert index.query("   ", top_k=5) == []
        assert index.query("weather", top_k=0) == []
        assert index.query("weather", top_k=-1) == []

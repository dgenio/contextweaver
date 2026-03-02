"""Shared pytest fixtures and configuration for contextweaver tests."""

from __future__ import annotations

import pytest

from contextweaver.config import ContextBudget, ContextPolicy, ScoringConfig
from contextweaver.context.manager import ContextManager
from contextweaver.routing.catalog import Catalog, generate_sample_catalog, load_catalog_dicts
from contextweaver.routing.graph import ChoiceGraph
from contextweaver.routing.tree import TreeBuilder
from contextweaver.store import StoreBundle
from contextweaver.store.artifacts import InMemoryArtifactStore
from contextweaver.store.episodic import InMemoryEpisodicStore
from contextweaver.store.event_log import InMemoryEventLog
from contextweaver.store.facts import InMemoryFactStore
from contextweaver.types import ContextItem, ItemKind, SelectableItem

# ------------------------------------------------------------------
# Basic stores
# ------------------------------------------------------------------


@pytest.fixture
def event_log() -> InMemoryEventLog:
    """Return a fresh empty event log."""
    return InMemoryEventLog()


@pytest.fixture
def artifact_store() -> InMemoryArtifactStore:
    """Return a fresh empty artifact store."""
    return InMemoryArtifactStore()


@pytest.fixture
def episodic_store() -> InMemoryEpisodicStore:
    """Return a fresh empty episodic store."""
    return InMemoryEpisodicStore()


@pytest.fixture
def fact_store() -> InMemoryFactStore:
    """Return a fresh empty fact store."""
    return InMemoryFactStore()


@pytest.fixture
def store_bundle(
    event_log: InMemoryEventLog,
    artifact_store: InMemoryArtifactStore,
    episodic_store: InMemoryEpisodicStore,
    fact_store: InMemoryFactStore,
) -> StoreBundle:
    """Return a StoreBundle wiring the four store fixtures together."""
    return StoreBundle(
        event_log=event_log,
        artifact_store=artifact_store,
        episodic_store=episodic_store,
        fact_store=fact_store,
    )


# ------------------------------------------------------------------
# Config defaults
# ------------------------------------------------------------------


@pytest.fixture
def default_budget() -> ContextBudget:
    """Return a default ContextBudget."""
    return ContextBudget()


@pytest.fixture
def default_policy() -> ContextPolicy:
    """Return a default ContextPolicy."""
    return ContextPolicy()


@pytest.fixture
def default_scoring() -> ScoringConfig:
    """Return a default ScoringConfig."""
    return ScoringConfig()


# ------------------------------------------------------------------
# Sample items (single)
# ------------------------------------------------------------------


@pytest.fixture
def sample_item() -> ContextItem:
    """Return a sample ContextItem for testing."""
    return ContextItem(
        id="item-1",
        kind=ItemKind.user_turn,
        text="Hello, how can I search the database?",
        token_estimate=10,
    )


@pytest.fixture
def sample_tool_result() -> ContextItem:
    """Return a sample tool_result ContextItem."""
    return ContextItem(
        id="result-1",
        kind=ItemKind.tool_result,
        text="status: ok\nresult: 42 rows found\n- row 1\n- row 2",
        token_estimate=20,
    )


# ------------------------------------------------------------------
# Diverse context items (for dedup / scoring / selection tests)
# ------------------------------------------------------------------


@pytest.fixture
def sample_context_items() -> list[ContextItem]:
    """Return 8+ ContextItems covering all ItemKinds.

    Includes a parent_id pair (tc1→tr1), two items with identical text
    for dedup testing, and a representative mix of kinds.
    """
    return [
        ContextItem(id="u1", kind=ItemKind.user_turn, text="Show me the sales report"),
        ContextItem(id="a1", kind=ItemKind.agent_msg, text="Fetching the report now."),
        ContextItem(
            id="tc1",
            kind=ItemKind.tool_call,
            text="get_report(type='sales', period='Q4')",
            parent_id="u1",
        ),
        ContextItem(
            id="tr1",
            kind=ItemKind.tool_result,
            text="Sales: $1.2M, Units: 8400, Growth: +12%",
            parent_id="tc1",
        ),
        ContextItem(
            id="ds1",
            kind=ItemKind.doc_snippet,
            text="The get_report tool returns JSON with sales, units, and growth fields.",
        ),
        ContextItem(
            id="mf1",
            kind=ItemKind.memory_fact,
            text="The user prefers tabular output format",
        ),
        ContextItem(
            id="ps1",
            kind=ItemKind.plan_state,
            text="Step 2 of 3: interpret tool output and format response",
        ),
        ContextItem(
            id="pol1",
            kind=ItemKind.policy,
            text="Always show currency values with two decimal places",
        ),
        # Duplicate text (for dedup testing)
        ContextItem(
            id="dup1",
            kind=ItemKind.doc_snippet,
            text="The get_report tool returns JSON with sales, units, and growth fields.",
        ),
    ]


# ------------------------------------------------------------------
# Selectable items & catalog
# ------------------------------------------------------------------

_NAMESPACES = ["db", "comms", "ml", "search", "billing", "admin"]


@pytest.fixture
def sample_selectable_items() -> list[SelectableItem]:
    """Return 30+ SelectableItems spread across 6 namespaces."""
    items: list[SelectableItem] = []
    counter = 0
    for ns in _NAMESPACES:
        for i in range(5):
            counter += 1
            items.append(
                SelectableItem(
                    id=f"{ns}_{i}",
                    kind="tool",
                    name=f"{ns}_action_{i}",
                    description=f"Perform action {i} in the {ns} namespace",
                    tags=[ns, f"tag-{i}"],
                    namespace=ns,
                    side_effects=(i % 3 == 0),
                )
            )
    return items


@pytest.fixture
def large_catalog() -> Catalog:
    """Return a Catalog with ~80 items from generate_sample_catalog."""
    dicts = generate_sample_catalog(n=80, seed=42)
    items = load_catalog_dicts(dicts)
    catalog = Catalog()
    for item in items:
        catalog.register(item)
    return catalog


# ------------------------------------------------------------------
# Graph
# ------------------------------------------------------------------


@pytest.fixture
def sample_graph(sample_selectable_items: list[SelectableItem]) -> ChoiceGraph:
    """Build a ChoiceGraph from the 30 sample selectable items."""
    return TreeBuilder(max_children=10).build(sample_selectable_items)


# ------------------------------------------------------------------
# Context manager
# ------------------------------------------------------------------


@pytest.fixture
def context_manager() -> ContextManager:
    """Return a fresh ContextManager with default config."""
    return ContextManager()


@pytest.fixture
def populated_manager() -> ContextManager:
    """Return a ContextManager pre-loaded with items, facts, and an episode."""
    mgr = ContextManager()
    mgr.ingest(ContextItem(id="u1", kind=ItemKind.user_turn, text="What is the weather?"))
    mgr.ingest(ContextItem(id="a1", kind=ItemKind.agent_msg, text="Let me check."))
    mgr.ingest(
        ContextItem(
            id="tc1",
            kind=ItemKind.tool_call,
            text="weather_api(city='London')",
            parent_id="u1",
        )
    )
    mgr.ingest(
        ContextItem(
            id="tr1",
            kind=ItemKind.tool_result,
            text="London: 15°C, partly cloudy, humidity 72%",
            parent_id="tc1",
        )
    )
    # Two facts
    mgr.add_fact("User prefers Celsius for temperature")
    mgr.add_fact("User is located in London, UK")
    # One episode
    mgr.add_episode("Previously asked about weather in Paris — answered 18°C sunny")
    return mgr

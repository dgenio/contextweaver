"""Shared pytest fixtures and configuration for contextweaver tests."""

from __future__ import annotations

import pytest
import pytest_asyncio

from contextweaver.config import ContextBudget, ContextPolicy, ScoringConfig
from contextweaver.context.manager import ContextManager
from contextweaver.routing.catalog import generate_sample_catalog, load_catalog_dicts
from contextweaver.store import StoreBundle
from contextweaver.store.artifacts import InMemoryArtifactStore
from contextweaver.store.episodic import InMemoryEpisodicStore
from contextweaver.store.event_log import InMemoryEventLog
from contextweaver.store.facts import InMemoryFactStore
from contextweaver.types import ContextItem, ItemKind, SelectableItem


@pytest.fixture
def store_bundle():
    """Return a StoreBundle with fresh in-memory stores."""
    return StoreBundle(
        artifact_store=InMemoryArtifactStore(),
        event_log=InMemoryEventLog(),
        episodic_store=InMemoryEpisodicStore(),
        fact_store=InMemoryFactStore(),
    )


@pytest.fixture
def artifact_store(store_bundle):
    """Return the artifact store from the bundle."""
    return store_bundle.artifact_store


@pytest.fixture
def event_log(store_bundle):
    """Return the event log from the bundle."""
    return store_bundle.event_log


@pytest.fixture
def episodic_store(store_bundle):
    """Return the episodic store from the bundle."""
    return store_bundle.episodic_store


@pytest.fixture
def fact_store(store_bundle):
    """Return the fact store from the bundle."""
    return store_bundle.fact_store


@pytest.fixture
def default_budget():
    """Return a default ContextBudget."""
    return ContextBudget()


@pytest.fixture
def default_policy():
    """Return a default ContextPolicy."""
    return ContextPolicy()


@pytest.fixture
def default_scoring():
    """Return a default ScoringConfig."""
    return ScoringConfig()


@pytest.fixture
def sample_item():
    """Return a sample ContextItem for testing."""
    return ContextItem(
        id="item-1",
        kind=ItemKind.USER_TURN,
        text="Hello, how can I search the database?",
        token_estimate=10,
    )


@pytest.fixture
def sample_tool_result():
    """Return a sample tool_result ContextItem."""
    return ContextItem(
        id="result-1",
        kind=ItemKind.TOOL_RESULT,
        text="status: ok\nresult: 42 rows found\n- row 1\n- row 2",
        token_estimate=20,
    )


@pytest.fixture
def sample_context_items():
    """8+ items covering all ItemKind values."""
    items = [
        ContextItem(
            id="ut1",
            kind=ItemKind.USER_TURN,
            text="Find my unpaid invoices",
            token_estimate=6,
            metadata={"timestamp": 1000.0, "tags": ["billing"]},
        ),
        ContextItem(
            id="tc1",
            kind=ItemKind.TOOL_CALL,
            text="Call billing.invoices.search",
            token_estimate=8,
            metadata={"timestamp": 1001.0, "tags": ["billing"]},
        ),
        ContextItem(
            id="tr1",
            kind=ItemKind.TOOL_RESULT,
            text="Found 47 unpaid invoices totaling $12,340",
            token_estimate=12,
            metadata={"timestamp": 1002.0, "tags": ["billing"]},
            parent_id="tc1",
        ),
        ContextItem(
            id="am1",
            kind=ItemKind.AGENT_MSG,
            text="I found 47 unpaid invoices.",
            token_estimate=8,
            metadata={"timestamp": 1003.0},
        ),
        ContextItem(
            id="ds1",
            kind=ItemKind.DOC_SNIPPET,
            text="Invoice API documentation snippet",
            token_estimate=10,
            metadata={"timestamp": 1004.0, "tags": ["docs"]},
        ),
        ContextItem(
            id="mf1",
            kind=ItemKind.MEMORY_FACT,
            text="user_name=Alice",
            token_estimate=4,
            metadata={"timestamp": 1005.0},
        ),
        ContextItem(
            id="ps1",
            kind=ItemKind.PLAN_STATE,
            text="Step 1: Search invoices. Step 2: Summarize.",
            token_estimate=12,
            metadata={"timestamp": 1006.0},
        ),
        ContextItem(
            id="po1",
            kind=ItemKind.POLICY,
            text="Always include invoice totals in responses",
            token_estimate=10,
            metadata={"timestamp": 1007.0},
        ),
        ContextItem(
            id="tr2",
            kind=ItemKind.TOOL_RESULT,
            text="Found 47 unpaid invoices totaling $12,340",
            token_estimate=12,
            metadata={"timestamp": 1008.0},
            parent_id="tc1",
        ),
        ContextItem(
            id="tr3",
            kind=ItemKind.TOOL_RESULT,
            text="Payment of $500 received",
            token_estimate=6,
            metadata={"timestamp": 1009.0, "tags": ["billing"]},
            parent_id="tc1",
            artifact_ref="art_001",
        ),
    ]
    return items


@pytest_asyncio.fixture
async def populated_event_log(event_log, sample_context_items):
    """An event log populated with sample_context_items."""
    for item in sample_context_items:
        await event_log.append(item)
    return event_log


@pytest.fixture
def sample_selectable_items():
    """30+ items, 5+ namespaces."""
    items = []
    namespaces = ["billing", "crm", "search", "docs", "admin", "comms"]
    for i, ns in enumerate(namespaces):
        for j in range(5):
            items.append(
                SelectableItem(
                    id=f"{ns}.tool_{j}",
                    kind="tool" if j % 3 != 0 else "agent",
                    name=f"{ns}.tool_{j}",
                    description=f"A {ns} tool that does thing {j}",
                    tags=[ns, f"tag_{j}"],
                    namespace=ns,
                )
            )
    return items


@pytest.fixture
def large_catalog():
    """Load a deterministic 80-item catalog."""
    return load_catalog_dicts(generate_sample_catalog(n=80, seed=42))


@pytest.fixture
def sample_graph(large_catalog):
    """Build a ChoiceGraph from the large catalog."""
    from contextweaver.routing.tree import TreeBuilder

    return TreeBuilder(max_children=10).build(large_catalog)


@pytest_asyncio.fixture
async def context_manager(store_bundle):
    """Return a fresh ContextManager with empty stores."""
    return ContextManager(stores=store_bundle)


@pytest_asyncio.fixture
async def populated_manager(context_manager, sample_context_items):
    """A ContextManager pre-populated with sample items and facts."""
    for item in sample_context_items:
        await context_manager.ingest(item)
    await context_manager.add_fact("user_name", "Alice")
    await context_manager.add_fact("account_id", "ACC-1234")
    await context_manager.add_episode("ep1", "User asked about unpaid invoices")
    return context_manager

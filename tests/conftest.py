"""Shared pytest fixtures and configuration for contextweaver tests."""

from __future__ import annotations

import pytest

from contextweaver.config import ContextBudget, ContextPolicy, ScoringConfig
from contextweaver.store.artifacts import InMemoryArtifactStore
from contextweaver.store.episodic import InMemoryEpisodicStore
from contextweaver.store.event_log import InMemoryEventLog
from contextweaver.store.facts import InMemoryFactStore
from contextweaver.types import ContextItem, ItemKind


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

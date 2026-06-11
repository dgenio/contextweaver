"""Tests for Python logging integration across contextweaver subsystems."""

from __future__ import annotations

import logging

import pytest

from contextweaver.adapters.a2a import a2a_agent_to_selectable, a2a_result_to_envelope
from contextweaver.adapters.mcp import mcp_result_to_envelope, mcp_tool_to_selectable
from contextweaver.config import ContextPolicy
from contextweaver.context.manager import ContextManager
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.store.artifacts import InMemoryArtifactStore
from contextweaver.store.episodic import InMemoryEpisodicStore
from contextweaver.store.event_log import InMemoryEventLog
from contextweaver.store.facts import Fact, InMemoryFactStore
from contextweaver.types import ContextItem, ItemKind, Phase, SelectableItem, Sensitivity

# ------------------------------------------------------------------
# Logger existence
# ------------------------------------------------------------------


@pytest.mark.parametrize(
    "logger_name",
    [
        "contextweaver.context",
        "contextweaver.routing",
        "contextweaver.store",
        "contextweaver.adapters",
    ],
)
def test_logger_exists(logger_name: str) -> None:
    """Each subsystem logger must be retrievable by name."""
    logger = logging.getLogger(logger_name)
    assert logger.name == logger_name


# ------------------------------------------------------------------
# Context pipeline logging
# ------------------------------------------------------------------


@pytest.mark.asyncio
async def test_context_build_emits_info(caplog: pytest.LogCaptureFixture) -> None:
    """A context build must emit an INFO-level summary."""
    log = InMemoryEventLog()
    log.append(ContextItem(id="u1", kind=ItemKind.user_turn, text="hello"))
    mgr = ContextManager(event_log=log)

    with caplog.at_level(logging.DEBUG, logger="contextweaver.context"):
        await mgr.build(phase=Phase.answer, query="hello")

    info_messages = [r for r in caplog.records if r.levelno == logging.INFO]
    assert any("context build" in r.message for r in info_messages)


@pytest.mark.asyncio
async def test_context_build_emits_debug_stages(caplog: pytest.LogCaptureFixture) -> None:
    """DEBUG messages must appear for pipeline stages during a build."""
    log = InMemoryEventLog()
    log.append(ContextItem(id="u1", kind=ItemKind.user_turn, text="search"))
    mgr = ContextManager(event_log=log)

    with caplog.at_level(logging.DEBUG, logger="contextweaver.context"):
        await mgr.build(phase=Phase.answer, query="search")

    messages = [r.message for r in caplog.records]
    assert any("generate_candidates" in m for m in messages)
    assert any("score_candidates" in m or "select_and_pack" in m for m in messages)


@pytest.mark.asyncio
async def test_context_build_no_text_content_logged(caplog: pytest.LogCaptureFixture) -> None:
    """Item text must NEVER appear in any log record."""
    secret_text = "SUPER_SECRET_CONTENT_12345"
    log = InMemoryEventLog()
    log.append(ContextItem(id="u1", kind=ItemKind.user_turn, text=secret_text))
    mgr = ContextManager(event_log=log)

    with caplog.at_level(logging.DEBUG, logger="contextweaver"):
        await mgr.build(phase=Phase.answer, query="test")

    for record in caplog.records:
        assert secret_text not in record.message, (
            f"Item text content leaked into log: {record.message!r}"
        )


@pytest.mark.asyncio
async def test_context_build_redact_mode_no_text_content_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """In redact mode, original item text must not appear in any log record."""
    secret_text = "REDACT_MODE_SECRET_67890"
    log = InMemoryEventLog()
    log.append(
        ContextItem(
            id="s1",
            kind=ItemKind.user_turn,
            text=secret_text,
            sensitivity=Sensitivity.internal,
        )
    )
    policy = ContextPolicy(
        sensitivity_floor=Sensitivity.internal,
        sensitivity_action="redact",
    )
    mgr = ContextManager(event_log=log, policy=policy)

    with caplog.at_level(logging.DEBUG, logger="contextweaver"):
        await mgr.build(phase=Phase.answer, query="test")

    for record in caplog.records:
        assert secret_text not in record.message, (
            f"Original text leaked into log in redact mode: {record.message!r}"
        )


# ------------------------------------------------------------------
# Ingest logging
# ------------------------------------------------------------------


def test_ingest_emits_debug(caplog: pytest.LogCaptureFixture) -> None:
    """Ingesting an item must emit a DEBUG log."""
    mgr = ContextManager()
    item = ContextItem(id="item-1", kind=ItemKind.user_turn, text="hello")

    with caplog.at_level(logging.DEBUG, logger="contextweaver.context"):
        mgr.ingest(item)

    assert any("ingest" in r.message and "item-1" in r.message for r in caplog.records)


def test_ingest_tool_result_emits_debug(caplog: pytest.LogCaptureFixture) -> None:
    """Ingesting a tool result must emit a DEBUG log with firewall status."""
    mgr = ContextManager()
    mgr.ingest(ContextItem(id="call-1", kind=ItemKind.tool_call, text="invoke tool"))

    with caplog.at_level(logging.DEBUG, logger="contextweaver.context"):
        mgr.ingest_tool_result("call-1", "short result", tool_name="test_tool")

    assert any("ingest_tool_result" in r.message for r in caplog.records)


# ------------------------------------------------------------------
# Routing logging
# ------------------------------------------------------------------


def test_augment_query_logs_original_and_augmented(caplog: pytest.LogCaptureFixture) -> None:
    """augment_query must DEBUG-log both the original and augmented query (issue #524)."""
    from contextweaver.routing.filters import augment_query

    with caplog.at_level(logging.DEBUG, logger="contextweaver.routing"):
        result = augment_query("find invoices", ["billing context"])

    assert result == "find invoices billing context"
    messages = [r.message for r in caplog.records]
    assert any("augment_query" in m and "find invoices" in m for m in messages)


def test_tree_builder_logs_fallback_strategy_at_info(caplog: pytest.LogCaptureFixture) -> None:
    """A non-namespace grouping strategy must be surfaced at INFO (issue #524)."""
    # No namespaces + more items than max_children forces the namespace
    # strategy to bail and a fallback (clustering/alphabetical) to run.
    items = [
        SelectableItem(id=f"t{i}", kind="tool", name=f"tool {i}", description=f"does thing {i}")
        for i in range(8)
    ]
    with caplog.at_level(logging.INFO, logger="contextweaver.routing"):
        TreeBuilder(max_children=3).build(items)

    info = [r for r in caplog.records if r.levelno == logging.INFO]
    assert any("fallback strategy" in r.message for r in info)


def test_navigator_logs_beam_pruning_at_debug(caplog: pytest.LogCaptureFixture) -> None:
    """Beam search must DEBUG-log per-step pruning counts (issue #524)."""
    items = [
        SelectableItem(
            id=f"ns{i % 3}.tool{i}",
            kind="tool",
            name=f"tool {i}",
            description=f"capability number {i}",
            namespace=f"ns{i % 3}",
        )
        for i in range(12)
    ]
    graph = TreeBuilder(max_children=3).build(items)
    router = Router(graph, items=items, beam_width=1)

    with caplog.at_level(logging.DEBUG, logger="contextweaver.routing"):
        router.route("tool 5")

    assert any("navigator.beam" in r.message for r in caplog.records)


def test_route_emits_info(caplog: pytest.LogCaptureFixture) -> None:
    """A route query must emit an INFO-level summary."""
    items = [
        SelectableItem(
            id="tool:search",
            kind="tool",
            name="search",
            description="Search the database",
            tags=["search"],
        ),
        SelectableItem(
            id="tool:write",
            kind="tool",
            name="write",
            description="Write to the database",
            tags=["write"],
        ),
    ]
    graph = TreeBuilder().build(items)
    router = Router(graph, items=items)

    with caplog.at_level(logging.INFO, logger="contextweaver.routing"):
        router.route("search")

    assert any("route" in r.message for r in caplog.records)


# ------------------------------------------------------------------
# Adapter logging
# ------------------------------------------------------------------


def test_mcp_tool_to_selectable_emits_debug(caplog: pytest.LogCaptureFixture) -> None:
    """MCP tool conversion must emit a DEBUG log."""
    tool_def = {"name": "github.search", "description": "Search repos"}

    with caplog.at_level(logging.DEBUG, logger="contextweaver.adapters"):
        mcp_tool_to_selectable(tool_def)

    assert any("mcp_tool_to_selectable" in r.message for r in caplog.records)


def test_mcp_result_to_envelope_emits_debug(caplog: pytest.LogCaptureFixture) -> None:
    """MCP result conversion must emit a DEBUG log."""
    result = {"content": [{"type": "text", "text": "found 3 repos"}]}

    with caplog.at_level(logging.DEBUG, logger="contextweaver.adapters"):
        mcp_result_to_envelope(result, "github.search")

    assert any("mcp_result_to_envelope" in r.message for r in caplog.records)


def test_a2a_agent_to_selectable_emits_debug(caplog: pytest.LogCaptureFixture) -> None:
    """A2A agent card conversion must emit a DEBUG log."""
    card = {"name": "summarizer", "description": "Summarize text"}

    with caplog.at_level(logging.DEBUG, logger="contextweaver.adapters"):
        a2a_agent_to_selectable(card)

    assert any("a2a_agent_to_selectable" in r.message for r in caplog.records)


def test_a2a_result_to_envelope_emits_debug(caplog: pytest.LogCaptureFixture) -> None:
    """A2A result conversion must emit a DEBUG log."""
    result = {"status": {"state": "completed"}, "artifacts": []}

    with caplog.at_level(logging.DEBUG, logger="contextweaver.adapters"):
        a2a_result_to_envelope(result, "summarizer")

    assert any("a2a_result_to_envelope" in r.message for r in caplog.records)


# ------------------------------------------------------------------
# Store logging
# ------------------------------------------------------------------


def test_event_log_append_emits_debug(caplog: pytest.LogCaptureFixture) -> None:
    """Event log append must emit a DEBUG log."""
    log = InMemoryEventLog()
    item = ContextItem(id="u1", kind=ItemKind.user_turn, text="hello")

    with caplog.at_level(logging.DEBUG, logger="contextweaver.store"):
        log.append(item)

    assert any("event_log.append" in r.message and "u1" in r.message for r in caplog.records)


def test_artifact_store_put_emits_debug(caplog: pytest.LogCaptureFixture) -> None:
    """Artifact store put must emit a DEBUG log."""
    store = InMemoryArtifactStore()

    with caplog.at_level(logging.DEBUG, logger="contextweaver.store"):
        store.put("handle:1", b"data", "text/plain", "test")

    assert any("artifact_store.put" in r.message for r in caplog.records)


def test_episodic_store_add_emits_debug(caplog: pytest.LogCaptureFixture) -> None:
    """Episodic store add must emit a DEBUG log."""
    from contextweaver.store.episodic import Episode

    store = InMemoryEpisodicStore()
    ep = Episode(episode_id="ep-1", summary="test episode")

    with caplog.at_level(logging.DEBUG, logger="contextweaver.store"):
        store.add(ep)

    assert any("episodic_store.add" in r.message for r in caplog.records)


def test_fact_store_put_emits_debug(caplog: pytest.LogCaptureFixture) -> None:
    """Fact store put must emit a DEBUG log."""
    store = InMemoryFactStore()
    fact = Fact(fact_id="f1", key="lang", value="python")

    with caplog.at_level(logging.DEBUG, logger="contextweaver.store"):
        store.put(fact)

    assert any("fact_store.put" in r.message for r in caplog.records)

"""Tests for FirewallStats + BuildStats/firewall integration (#402, #404, #406)."""

from __future__ import annotations

import pytest

from contextweaver import BuildStats, ContextManager, FirewallStats
from contextweaver.context.firewall import apply_firewall
from contextweaver.exceptions import DeterminismError
from contextweaver.store.artifacts import InMemoryArtifactStore
from contextweaver.types import ContextItem, ItemKind, Phase


class _FakeLlmSummarizer:
    is_llm = True

    def summarize(self, raw: str, metadata: dict) -> str:
        return "LLM SUMMARY"


# ---------------------------------------------------------------------------
# FirewallStats dataclass
# ---------------------------------------------------------------------------


def test_firewall_stats_round_trips() -> None:
    fs = FirewallStats(
        triggered=True,
        strategy="structured",
        threshold_chars=2000,
        original_chars=7304,
        original_tokens=2549,
        summary_chars=186,
        summary_tokens=47,
        artifact_ref="artifact:tr_invoices",
        summarized_by_llm=False,
    )
    assert FirewallStats.from_dict(fs.to_dict()) == fs


def test_firewall_stats_savings_properties_clamp_at_zero() -> None:
    fs = FirewallStats(
        triggered=True,
        strategy="summary",
        original_chars=100,
        original_tokens=25,
        summary_chars=10,
        summary_tokens=3,
    )
    assert fs.chars_saved == 90
    assert fs.tokens_saved == 22
    # Pass-through (summary == original) never reports negative savings.
    noop = FirewallStats(triggered=False, strategy="passthrough", original_chars=5, summary_chars=5)
    assert noop.chars_saved == 0


# ---------------------------------------------------------------------------
# apply_firewall now emits FirewallStats (#402) and honours determinism (#404)
# ---------------------------------------------------------------------------


def test_apply_firewall_emits_firewall_stats() -> None:
    item = ContextItem(id="r1", kind=ItemKind.tool_result, text="x" * 5000)
    env = apply_firewall(item, InMemoryArtifactStore(), threshold_chars=2000)[1]
    assert env is not None and env.firewall_stats is not None
    assert env.firewall_stats.triggered is True
    assert env.firewall_stats.strategy == "summary"
    assert env.firewall_stats.threshold_chars == 2000
    assert env.firewall_stats.original_chars == 5000


def test_apply_firewall_structured_strategy_via_keep() -> None:
    item = ContextItem(id="r2", kind=ItemKind.tool_result, text='{"a": {"keep": 1, "drop": 2}}')
    _, env = apply_firewall(item, InMemoryArtifactStore(), keep=["a.keep"])
    assert env is not None and env.firewall_stats is not None
    assert env.firewall_stats.strategy == "structured"
    assert env.summary == '{"a": {"keep": 1}}'


def test_apply_firewall_empty_keep_falls_through_to_summary() -> None:
    # keep=[] must NOT select the structured strategy (which would raise inside
    # the projection and get swallowed into a partial summary).
    item = ContextItem(id="rk", kind=ItemKind.tool_result, text="status: ok\ncount: 5")
    _, env = apply_firewall(item, InMemoryArtifactStore(), keep=[])
    assert env is not None and env.firewall_stats is not None
    assert env.firewall_stats.strategy == "summary"
    assert env.status == "ok"
    assert len(env.facts) >= 1


def test_apply_firewall_deterministic_raises_on_llm() -> None:
    item = ContextItem(id="r3", kind=ItemKind.tool_result, text="some output")
    with pytest.raises(DeterminismError):
        apply_firewall(
            item, InMemoryArtifactStore(), summarizer=_FakeLlmSummarizer(), deterministic=True
        )


def test_apply_firewall_deterministic_allows_structured_with_llm_present() -> None:
    item = ContextItem(id="r4", kind=ItemKind.tool_result, text='{"keep": 1, "drop": 2}')
    _, env = apply_firewall(
        item,
        InMemoryArtifactStore(),
        summarizer=_FakeLlmSummarizer(),
        deterministic=True,
        keep=["keep"],
    )
    assert env is not None and env.firewall_stats is not None
    assert env.firewall_stats.strategy == "structured"


# ---------------------------------------------------------------------------
# BuildStats.firewall_summary aggregation (#402)
# ---------------------------------------------------------------------------


def test_build_stats_firewall_summary_empty() -> None:
    summary = BuildStats().firewall_summary()
    assert summary.triggered is False
    assert summary.strategy == "noop"


def test_build_stats_firewall_summary_aggregates() -> None:
    stats = BuildStats(
        firewall_events=[
            FirewallStats(triggered=True, strategy="summary", original_chars=100, summary_chars=10),
            FirewallStats(triggered=True, strategy="summary", original_chars=200, summary_chars=20),
        ]
    )
    summary = stats.firewall_summary()
    assert summary.triggered is True
    assert summary.strategy == "summary"
    assert summary.original_chars == 300
    assert summary.summary_chars == 30


def test_build_stats_firewall_summary_mixed_strategies() -> None:
    stats = BuildStats(
        firewall_events=[
            FirewallStats(triggered=True, strategy="summary"),
            FirewallStats(triggered=True, strategy="structured"),
        ]
    )
    assert stats.firewall_summary().strategy == "mixed"


def test_build_stats_round_trips_firewall_events() -> None:
    stats = BuildStats(
        firewall_events=[FirewallStats(triggered=True, strategy="structured", original_chars=9)]
    )
    restored = BuildStats.from_dict(stats.to_dict())
    assert restored.firewall_events == stats.firewall_events


# ---------------------------------------------------------------------------
# End-to-end: ContextManager build surfaces firewall_events (#402)
# ---------------------------------------------------------------------------


def test_manager_build_populates_firewall_events() -> None:
    # A raw tool_result ingested without pre-firewalling is firewalled *during*
    # the build, so its FirewallStats surfaces on BuildStats.firewall_events.
    mgr = ContextManager()
    mgr.ingest_sync(ContextItem(id="result:tc1", kind=ItemKind.tool_result, text="x" * 5000))
    pack = mgr.build_sync(phase=Phase.interpret, query="summarise the dump")
    assert len(pack.stats.firewall_events) == 1
    assert pack.stats.firewall_summary().triggered is True


def test_manager_deterministic_build_raises_on_llm_summarizer() -> None:
    mgr = ContextManager(summarizer=_FakeLlmSummarizer(), deterministic=True)
    mgr.ingest_sync(ContextItem(id="result:tc1", kind=ItemKind.tool_result, text="x" * 5000))
    with pytest.raises(DeterminismError):
        mgr.build_sync(phase=Phase.interpret, query="summarise")


def test_manager_structured_ingest_uses_projection() -> None:
    from contextweaver.summarize.structured import StructuredFirewall

    mgr = ContextManager()
    _item, env = mgr.ingest_tool_result_sync(
        "tc1",
        '{"keep": 1, "drop": ' + '"' + "x" * 5000 + '"}',
        media_type="application/json",
        firewall_threshold=100,
        firewall=StructuredFirewall(keep=["keep"]),
    )
    assert env.firewall_stats is not None
    assert env.firewall_stats.strategy == "structured"
    assert env.summary == '{"keep": 1}'

"""Shared deterministic firewall measurement for benchmark scripts."""

from __future__ import annotations

from dataclasses import dataclass

from contextweaver.context.manager import ContextManager
from contextweaver.protocols import CharDivFourEstimator


@dataclass(frozen=True)
class FirewallMeasurement:
    """Observed firewall and artifact-view behavior for one tool result."""

    raw_chars: int
    summary_chars: int
    reduction_pct: float
    artifact_created: bool
    raw_exposed_inline: bool
    tool_view_recovered: bool


def measure_firewall(raw_text: str, *, tool_name: str) -> FirewallMeasurement:
    """Ingest *raw_text*, then verify its out-of-band artifact is viewable."""
    manager = ContextManager(estimator=CharDivFourEstimator(), deterministic=True)
    item, _envelope = manager.ingest_tool_result_sync(
        "benchmark-call",
        raw_text,
        tool_name=tool_name,
        firewall_threshold=2000,
    )
    handle = item.artifact_ref.handle if item.artifact_ref is not None else None
    view_recovered = False
    if handle is not None:
        viewed = manager.drilldown_sync(handle, {"type": "head", "chars": 80})
        view_recovered = viewed == raw_text[:80]
    raw_chars = len(raw_text)
    summary_chars = len(item.text)
    reduction = 100.0 * (1.0 - summary_chars / max(raw_chars, 1))
    return FirewallMeasurement(
        raw_chars=raw_chars,
        summary_chars=summary_chars,
        reduction_pct=round(reduction, 2),
        artifact_created=handle is not None,
        raw_exposed_inline=item.text == raw_text,
        tool_view_recovered=view_recovered,
    )

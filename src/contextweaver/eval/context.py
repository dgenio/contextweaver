"""Context-build evaluation harness (issue #12).

:func:`evaluate_context` runs a single :class:`~contextweaver.context.manager.ContextManager`
build for a given phase/query and reports how the compiled prompt compares
to a naive "concatenate every event" baseline:

- **prompt_tokens / budget_tokens / budget_utilization_pct** — how full the
  phase budget is after compilation.
- **naive_tokens / token_savings / token_savings_pct** — tokens the firewall
  and selection saved versus dumping the entire event log into the prompt.
- **items kept / dropped / deduped** — selection diagnostics lifted straight
  from :class:`~contextweaver.envelope.BuildStats`.

The naive baseline is estimated over ``manager.event_log.all()`` so it
reflects exactly the material the manager had available.  Given a
deterministic estimator the report is reproducible.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from contextweaver.context.manager import ContextManager
from contextweaver.protocols import CharDivFourEstimator, TokenEstimator
from contextweaver.types import Phase

__all__ = ["ContextEvalReport", "evaluate_context"]


@dataclass
class ContextEvalReport:
    """Token-budget and selection metrics for one context build."""

    phase: str = Phase.answer.value
    prompt_tokens: int = 0
    budget_tokens: int = 0
    budget_utilization_pct: float = 0.0
    naive_tokens: int = 0
    token_savings: int = 0
    token_savings_pct: float = 0.0
    total_candidates: int = 0
    items_included: int = 0
    items_dropped: int = 0
    dedup_removed: int = 0

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "phase": self.phase,
            "prompt_tokens": self.prompt_tokens,
            "budget_tokens": self.budget_tokens,
            "budget_utilization_pct": self.budget_utilization_pct,
            "naive_tokens": self.naive_tokens,
            "token_savings": self.token_savings,
            "token_savings_pct": self.token_savings_pct,
            "total_candidates": self.total_candidates,
            "items_included": self.items_included,
            "items_dropped": self.items_dropped,
            "dedup_removed": self.dedup_removed,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContextEvalReport:
        """Build a :class:`ContextEvalReport` from a raw dict."""
        return cls(
            phase=str(data.get("phase", Phase.answer.value)),
            prompt_tokens=int(data.get("prompt_tokens", 0)),
            budget_tokens=int(data.get("budget_tokens", 0)),
            budget_utilization_pct=float(data.get("budget_utilization_pct", 0.0)),
            naive_tokens=int(data.get("naive_tokens", 0)),
            token_savings=int(data.get("token_savings", 0)),
            token_savings_pct=float(data.get("token_savings_pct", 0.0)),
            total_candidates=int(data.get("total_candidates", 0)),
            items_included=int(data.get("items_included", 0)),
            items_dropped=int(data.get("items_dropped", 0)),
            dedup_removed=int(data.get("dedup_removed", 0)),
        )

    def summary(self) -> str:
        """Return a compact, human-readable one-block summary."""
        return (
            f"Context eval (phase={self.phase}): "
            f"{self.prompt_tokens}/{self.budget_tokens} tokens "
            f"({self.budget_utilization_pct:.1f}% of budget)\n"
            f"  naive_tokens={self.naive_tokens}  "
            f"savings={self.token_savings} ({self.token_savings_pct:.1f}%)\n"
            f"  candidates={self.total_candidates}  "
            f"included={self.items_included}  "
            f"dropped={self.items_dropped}  "
            f"dedup_removed={self.dedup_removed}"
        )


def evaluate_context(
    manager: ContextManager,
    phase: Phase = Phase.answer,
    query: str = "",
    *,
    estimator: TokenEstimator | None = None,
) -> ContextEvalReport:
    """Build context for *phase*/*query* and report budget + selection metrics.

    Args:
        manager: A context manager whose event log has already been
            populated by the caller.
        phase: Phase whose budget the build targets.
        query: Query string scored against candidate items.
        estimator: Token estimator used only for the naive-concatenation
            baseline.  Defaults to :class:`CharDivFourEstimator`.  The
            *compiled* token count always comes from the build's own
            :class:`~contextweaver.envelope.BuildStats`.

    Returns:
        A :class:`ContextEvalReport`.
    """
    est = estimator if estimator is not None else CharDivFourEstimator()

    pack = manager.build_sync(phase=phase, query=query)
    stats = pack.stats

    naive_text = "\n".join(item.text for item in manager.event_log.all())
    naive_tokens = est.estimate(naive_text)

    prompt_tokens = stats.prompt_tokens
    budget_tokens = manager.budget.for_phase(phase)
    token_savings = naive_tokens - prompt_tokens
    savings_pct = round(token_savings / naive_tokens * 100, 1) if naive_tokens else 0.0
    utilization = round(prompt_tokens / budget_tokens * 100, 1) if budget_tokens else 0.0

    return ContextEvalReport(
        phase=phase.value,
        prompt_tokens=prompt_tokens,
        budget_tokens=budget_tokens,
        budget_utilization_pct=utilization,
        naive_tokens=naive_tokens,
        token_savings=token_savings,
        token_savings_pct=savings_pct,
        total_candidates=stats.total_candidates,
        items_included=stats.included_count,
        items_dropped=stats.dropped_count,
        dedup_removed=stats.dedup_removed,
    )

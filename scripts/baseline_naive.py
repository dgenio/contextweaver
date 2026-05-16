"""Naïve concat baseline for benchmark scenarios (issue #215).

Per-scenario quality proxy that contextweaver's reported numbers can be
defended against without involving an LLM judge (which is forbidden by
``benchmarks/README.md``'s "no LLM calls" policy):

* ``naive_tokens`` — ``CharDivFourEstimator`` token count of "concatenate
  every event's ``text`` field" — the simplest possible baseline an
  integrator could write before adopting contextweaver.
* ``cw_tokens`` — the prompt-token count that ``ContextManager.build_sync``
  already reports (also via ``CharDivFourEstimator``).
* ``pct_reduction`` — ``(naive - cw) / naive * 100``, clamped to ``[0,100]``
  so the published number can never claim a negative reduction (which
  would be a regression, not a saving).
* ``coverage_pct`` — fraction of parent-id chains in the input that
  survive in the rendered prompt. Loosely: "of the messages that depend
  on an earlier message, how many keep their dependency in scope?"

Both ``naive_tokens`` and ``cw_tokens`` go through the same estimator so
the ratio is meaningful regardless of whether ``tiktoken``'s BPE table
is cached locally — matching the harness's "numbers do not depend on
tiktoken's cached encoding state" framing in ``benchmarks/scorecard.md``.
The issue specification calls for ``cl100k_base`` token counting;
``CharDivFourEstimator`` is the harness-wide proxy and gives directly
comparable numerator and denominator.

This module lives under ``scripts/`` (and is invoked from
``benchmarks/benchmark.py``) rather than ``src/contextweaver/`` because
it is benchmark tooling, not library API. The :class:`NaiveDelta` shape
and :func:`compute_naive_delta` are the only public surface.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from contextweaver.envelope import ContextPack
    from contextweaver.types import ContextItem


def _estimate_tokens(text: str) -> int:
    """One token per four input characters — matches ``CharDivFourEstimator``."""
    return len(text) // 4


@dataclass
class NaiveDelta:
    """One scenario's naïve-baseline comparison block."""

    naive_tokens: int
    cw_tokens: int
    pct_reduction: float
    coverage_pct: float


def _naive_concat_tokens(events: list[ContextItem]) -> int:
    """Token count of "concatenate every event's text" — the dumb baseline."""
    blob = "\n".join(ev.text for ev in events)
    return _estimate_tokens(blob)


def _coverage_pct(events: list[ContextItem], pack: ContextPack) -> float:
    """Return the fraction of parent-id chains preserved in *pack*.

    For every event with a ``parent_id``, this asks: does a substantive
    prefix of the parent event's ``text`` appear in the rendered prompt?
    Substring matching uses the first 40 characters of the parent's text
    to keep the check robust against the firewall's summary rendering
    (which can rewrite long tool outputs but preserves opening lines).

    Edge case: scenarios with no parent_id chains return ``100.0`` (there
    is no coverage to lose). Empty parent text → counted as kept (vacuous
    truth).
    """
    children_with_parent = [ev for ev in events if ev.parent_id]
    if not children_with_parent:
        return 100.0
    by_id = {ev.id: ev for ev in events}
    prompt = pack.prompt
    kept = 0
    for child in children_with_parent:
        assert child.parent_id is not None  # narrowed by the comprehension above
        parent = by_id.get(child.parent_id)
        if parent is None or not parent.text:
            kept += 1  # parent is unresolvable in the scenario — vacuously kept
            continue
        # The first 40 chars are enough to disambiguate; longer prefixes
        # over-fit to exact prompt rendering and break under summarization.
        probe = parent.text[:40]
        if probe in prompt:
            kept += 1
    return round(kept / len(children_with_parent) * 100, 2)


def compute_naive_delta(
    *,
    events: list[ContextItem],
    pack: ContextPack,
    cw_tokens: int,
) -> NaiveDelta:
    """Compute the naïve-baseline delta for one scenario.

    Args:
        events: The full scenario event list (input to ``ContextManager``).
        pack: The :class:`ContextPack` produced by ``build_sync``.
        cw_tokens: Token count of ``pack.prompt`` as already measured by
            the benchmark harness (avoids a redundant tokenizer round-trip).

    Returns:
        A :class:`NaiveDelta` with all four fields populated. The
        percentage fields are rounded to two decimals so the JSON output
        is byte-stable across runs.
    """
    naive = _naive_concat_tokens(events)
    # Clamp pct_reduction so a published baseline can never claim a
    # negative reduction (which would imply contextweaver wrote *more*
    # tokens than the dumb concat — possible on tiny scenarios where the
    # render headers dominate, but not a defensible "saving" to publish).
    pct = max(0.0, (naive - cw_tokens) / naive * 100) if naive > 0 else 0.0
    coverage = _coverage_pct(events, pack)
    return NaiveDelta(
        naive_tokens=int(naive),
        cw_tokens=int(cw_tokens),
        pct_reduction=round(pct, 2),
        coverage_pct=coverage,
    )

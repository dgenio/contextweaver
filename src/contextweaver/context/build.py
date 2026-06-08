"""Core Context Engine build pipeline.

Extracted from :mod:`contextweaver.context.manager` so that ``manager.py``
stays close to the project's <=300 lines per module guideline (see AGENTS.md
and issue #101).  :class:`~contextweaver.context.manager.ContextManager`
keeps the public ``build`` / ``build_sync`` methods as thin delegations to
:func:`run_build_pipeline`; this module is not part of the public API.

The pipeline runs the eight Context Engine stages plus episodic/fact header
assembly and optional explanation capture.  It operates on a
:class:`ContextManager`'s internals directly (it needs eleven stores/configs,
too many to thread as positional parameters); the coupling is intentional and
private.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from contextweaver.config import ContextBudget
from contextweaver.context.candidates import generate_candidates, resolve_dependency_closure
from contextweaver.context.dedup import deduplicate_candidates
from contextweaver.context.explanation import build_explanation as _build_explanation
from contextweaver.context.firewall import apply_firewall_to_batch
from contextweaver.context.prompt import render_context
from contextweaver.context.scoring import score_candidates
from contextweaver.context.selection import select_and_pack
from contextweaver.context.sensitivity import apply_sensitivity_filter
from contextweaver.envelope import ContextPack
from contextweaver.types import Phase

if TYPE_CHECKING:
    from contextweaver.context._manager_base import _ManagerState
    from contextweaver.context.explanation import ContextBuildExplanation

logger = logging.getLogger("contextweaver.context")

# Maximum facts injected into the prompt header to prevent unbounded growth.
_MAX_FACT_LINES: int = 64
_MAX_FACT_CHARS: int = 2000


def run_build_pipeline(
    manager: _ManagerState,
    *,
    phase: Phase = Phase.answer,
    query: str = "",
    query_tags: list[str] | None = None,
    header: str = "",
    footer: str = "",
    budget_tokens: int | None = None,
    hints: list[str] | None = None,
    extra: dict[str, Any] | None = None,
    explain: bool = False,
) -> tuple[ContextPack, ContextBuildExplanation | None]:
    """Run the full eight-stage context build (synchronous core).

    See :meth:`ContextManager.build` for parameter semantics. Returns a
    ``(pack, explanation)`` tuple; *explanation* is ``None`` unless *explain*.

    Args:
        manager: The ContextManager's internal state (stores, config, hooks).
        phase: The execution phase determining which candidates are eligible.
        query: The user/agent query string for relevance scoring.
        query_tags: Optional tags extracted from the query.
        header: Caller-provided header text (prepended to the prompt).
        footer: Caller-provided footer text (appended to the prompt).
        budget_tokens: **Per-phase token budget override.**

            When provided, this value replaces the phase-specific budget from
            ``manager._budget`` for the current build only. The override applies
            to the phase specified by the *phase* argument:

            * ``Phase.route`` → overrides ``budget.route``
            * ``Phase.call`` → overrides ``budget.call``
            * ``Phase.interpret`` → overrides ``budget.interpret``
            * ``Phase.answer`` → overrides ``budget.answer``

            This is useful for ad-hoc builds where the default budget is too
            restrictive or too generous. The override does not persist across
            builds; subsequent calls use the manager's configured budget unless
            overridden again.

            Example::

                # Use a larger budget for this specific answer build
                pack, _ = manager.build(
                    phase=Phase.answer,
                    query="Summarize this long document",
                    budget_tokens=8000,  # Override default 4096
                )

        hints: Optional hints for the router/scorer.
        extra: Reserved for future use.
        explain: If True, return a detailed explanation of the build process.

    Returns:
        A tuple of (:class:`~contextweaver.envelope.ContextPack`,
        :class:`~contextweaver.context.explanation.ContextBuildExplanation` | None).

    See Also:
        :meth:`contextweaver.context.manager.ContextManager.build` — public API.
        :class:`contextweaver.config.ContextBudget` — per-phase budget configuration.
    """
    _ = extra  # reserved
    _tags = sorted(set(list(query_tags or []) + list(hints or [])))

    # Override budget if requested
    effective_budget = manager._budget
    if budget_tokens is not None:
        effective_budget = ContextBudget(
            route=budget_tokens if phase == Phase.route else manager._budget.route,
            call=budget_tokens if phase == Phase.call else manager._budget.call,
            interpret=budget_tokens if phase == Phase.interpret else manager._budget.interpret,
            answer=budget_tokens if phase == Phase.answer else manager._budget.answer,
        )

    # 1. Generate candidates
    candidates = generate_candidates(manager._event_log, phase, manager._policy)

    # 2. Dependency closure
    if explain:
        pre_closure_ids = {c.id for c in candidates}
    candidates, closures = resolve_dependency_closure(candidates, manager._event_log)
    closure_added_ids = {c.id for c in candidates} - pre_closure_ids if explain else set()

    # 3. Sensitivity filter
    if explain:
        pre_sens_ids = {(c.id, c.kind.value, c.sensitivity.value) for c in candidates}
    candidates, sensitivity_drops = apply_sensitivity_filter(candidates, manager._policy)
    if explain:
        post_sens_ids = {c.id for c in candidates}
        sensitivity_dropped_records: list[tuple[str, str, str]] = sorted(
            (cid, kind, sens) for (cid, kind, sens) in pre_sens_ids if cid not in post_sens_ids
        )
    else:
        sensitivity_dropped_records = []

    # 4. Firewall
    candidates, envelopes = apply_firewall_to_batch(
        candidates,
        manager._artifact_store,
        manager._hook,
        summarizer=manager._summarizer,
        extractor=manager._extractor,
    )

    # 5. Score
    scored = score_candidates(candidates, query, _tags, manager._scoring)

    # 6. Dedup
    if explain:
        pre_dedup_view: list[tuple[str, str, str, float]] = [
            (item.id, item.kind.value, item.sensitivity.value, score) for score, item in scored
        ]
    scored, dedup_removed = deduplicate_candidates(
        scored, similarity_threshold=manager._scoring.dedup_threshold
    )
    if explain:
        post_dedup_ids = {item.id for _score, item in scored}
        dedup_dropped_records: list[tuple[str, str, str, float]] = [
            (iid, kind, sens, sc)
            for (iid, kind, sens, sc) in pre_dedup_view
            if iid not in post_dedup_ids
        ]
    else:
        dedup_dropped_records = []

    # Pre-build episodic + fact injection text so we can estimate its
    # token cost and subtract it from the budget *before* selection.
    full_header, hf_tokens = _assemble_header(manager, header, footer)

    # Subtract header/footer overhead from the effective budget so that
    # select_and_pack only fills the remaining space.
    adjusted = _adjust_budget_for_header(effective_budget, phase, hf_tokens)

    # 7. Select (budget already accounts for header/footer overhead)
    selected, stats = select_and_pack(scored, phase, adjusted, manager._policy, manager._estimator)
    stats.dedup_removed = dedup_removed
    stats.dependency_closures = closures
    stats.header_footer_tokens = hf_tokens
    if sensitivity_drops > 0:
        # Account for items dropped by sensitivity filtering in both the
        # total candidate count and the drop breakdown so that
        # dropped_count + included_count <= total_candidates remains true.
        stats.total_candidates += sensitivity_drops
        stats.dropped_count += sensitivity_drops
        stats.dropped_reasons["sensitivity"] = (
            stats.dropped_reasons.get("sensitivity", 0) + sensitivity_drops
        )

    # 8. Render
    prompt = render_context(selected, header=full_header, footer=footer)
    pack = ContextPack(prompt=prompt, stats=stats, phase=phase, envelopes=envelopes)

    # Assemble the explanation (issue #291) only when requested.
    explanation: ContextBuildExplanation | None = None
    if explain:
        explanation = _build_explanation(
            phase=phase,
            query=query,
            stats=stats,
            sensitivity_dropped=sensitivity_dropped_records,
            sensitivity_drops=sensitivity_drops,
            dedup_dropped=dedup_dropped_records,
            dedup_removed=dedup_removed,
            closures=closures,
            closure_added_ids=closure_added_ids,
            scored=scored,
            selected_ids={item.id for item in selected},
            budget_tokens=adjusted.for_phase(phase),
        )

    manager._hook.on_context_built(pack)
    logger.info(
        "context build: phase=%s, included=%d, dropped=%d, tokens=%d/%d",
        phase.value,
        stats.included_count,
        stats.dropped_count,
        sum(stats.tokens_per_section.values()),
        effective_budget.for_phase(phase),
    )
    return pack, explanation


def _assemble_header(manager: _ManagerState, header: str, footer: str) -> tuple[str, int]:
    """Build the full prompt header (episodic + facts + caller header).

    Returns ``(full_header, header_footer_token_estimate)``.
    """
    extra_sections: list[str] = []

    # Episodic summaries (latest 3)
    episodic_entries = manager._episodic_store.latest(3)
    if episodic_entries:
        ep_lines = ["[EPISODIC MEMORY]"]
        for _ep_id, ep_summary, _meta in episodic_entries:
            ep_lines.append(f"- {ep_summary}")
        extra_sections.append("\n".join(ep_lines))

    # Facts snapshot — capped to avoid unbounded prompt growth.
    all_facts = manager._fact_store.all()
    if all_facts:
        fact_lines: list[str] = ["[FACTS]"]
        total_chars = len(fact_lines[0])
        for idx, fact in enumerate(all_facts):
            if idx >= _MAX_FACT_LINES:
                remaining = len(all_facts) - idx
                if remaining > 0:
                    fact_lines.append(f"- ... ({remaining} more facts omitted)")
                break
            line = f"- {fact.key}: {fact.value}"
            if total_chars + len(line) > _MAX_FACT_CHARS:
                fact_lines.append("- ... (facts truncated to fit header budget)")
                break
            fact_lines.append(line)
            total_chars += len(line)
        extra_sections.append("\n".join(fact_lines))

    full_header = header
    if extra_sections:
        prefix = "\n\n".join(extra_sections)
        full_header = f"{prefix}\n\n{header}" if header else prefix

    hf_tokens = 0
    if full_header:
        hf_tokens += manager._estimator.estimate(full_header)
    if footer:
        hf_tokens += manager._estimator.estimate(footer)
    return full_header, hf_tokens


def _adjust_budget_for_header(
    effective_budget: ContextBudget, phase: Phase, hf_tokens: int
) -> ContextBudget:
    """Subtract header/footer token overhead from the active phase's budget."""
    if hf_tokens <= 0:
        return effective_budget
    return ContextBudget(
        route=max(effective_budget.route - hf_tokens, 0)
        if phase == Phase.route
        else effective_budget.route,
        call=max(effective_budget.call - hf_tokens, 0)
        if phase == Phase.call
        else effective_budget.call,
        interpret=max(effective_budget.interpret - hf_tokens, 0)
        if phase == Phase.interpret
        else effective_budget.interpret,
        answer=max(effective_budget.answer - hf_tokens, 0)
        if phase == Phase.answer
        else effective_budget.answer,
    )

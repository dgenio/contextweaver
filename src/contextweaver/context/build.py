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
from dataclasses import replace
from typing import TYPE_CHECKING, Any

from contextweaver.config import ContextPolicy
from contextweaver.context.build_policy import (
    adjust_budget_for_header,
    enforce_overflow_policy,
    override_phase_budget,
    render_pack_prompt,
)
from contextweaver.context.candidates import generate_candidates, resolve_dependency_closure
from contextweaver.context.dedup import deduplicate_candidates
from contextweaver.context.explanation import build_explanation as _build_explanation
from contextweaver.context.firewall import apply_firewall_to_batch
from contextweaver.context.scoring import score_candidates
from contextweaver.context.selection import select_and_pack
from contextweaver.context.sensitivity import _SENSITIVITY_ORDER, apply_sensitivity_filter
from contextweaver.envelope import BuildStats, ContextPack, DroppedItem
from contextweaver.exceptions import ContextWeaverError
from contextweaver.protocols import SensitivityClassifier
from contextweaver.tokens import estimator_name
from contextweaver.types import ContextItem, ItemKind, Phase, Sensitivity

if TYPE_CHECKING:
    from collections.abc import Callable

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
    renderer: Callable[[list[ContextItem]], str] | None = None,
) -> tuple[ContextPack, ContextBuildExplanation | None]:
    """Run the full eight-stage context build (synchronous core).

    See :meth:`ContextManager.build` for parameter semantics (including the
    caller-owned *renderer* hook, issue #410). Returns a ``(pack, explanation)``
    tuple; *explanation* is ``None`` unless *explain*.
    """
    _ = extra  # reserved
    _tags = sorted(set(list(query_tags or []) + list(hints or [])))

    # Override the active phase's budget if requested (issue #412 semantics).
    effective_budget = override_phase_budget(manager._budget, phase, budget_tokens)

    # 1. Generate candidates
    candidates = generate_candidates(manager._event_log, phase, manager._policy)

    # 2. Dependency closure
    if explain:
        pre_closure_ids = {c.id for c in candidates}
    candidates, closures = resolve_dependency_closure(candidates, manager._event_log)
    total_candidates = len(candidates)
    closure_added_ids = {c.id for c in candidates} - pre_closure_ids if explain else set()

    # 3. Sensitivity classification (issue #542) + filter.  Classification runs
    # first so unlabelled content is raised to an appropriate level *before*
    # enforcement; the explanation then reflects the labels actually enforced.
    if manager._sensitivity_classifier is not None:
        candidates = _classify_items(candidates, manager._sensitivity_classifier)
    pre_sensitivity = list(candidates)
    if explain:
        pre_sens_ids = {(c.id, c.kind.value, c.sensitivity.value) for c in pre_sensitivity}
    candidates, sensitivity_drops = apply_sensitivity_filter(
        candidates, manager._policy, manager._estimator
    )
    post_sensitivity_ids = {item.id for item in candidates}
    sensitivity_dropped_items = [
        item for item in pre_sensitivity if item.id not in post_sensitivity_ids
    ]
    if explain:
        sensitivity_dropped_records: list[tuple[str, str, str]] = sorted(
            (cid, kind, sens)
            for (cid, kind, sens) in pre_sens_ids
            if cid not in post_sensitivity_ids
        )
    else:
        sensitivity_dropped_records = []

    # 4. Firewall
    candidates, envelopes = apply_firewall_to_batch(
        candidates,
        manager._artifact_store,
        manager._hook,
        view_registry=manager._view_registry,
        summarizer=manager._summarizer,
        extractor=manager._extractor,
        deterministic=manager._deterministic,
        redact_secrets=manager._redact_secrets,
    )

    # 5. Score — resolve any per-phase weight override first (issue #487);
    # dedup still uses the base config's threshold.
    effective_scoring = manager._scoring.resolved_for_phase(phase)
    scored = score_candidates(candidates, query, _tags, effective_scoring)

    # 6. Dedup
    pre_dedup_scored = list(scored)
    if explain:
        pre_dedup_view: list[tuple[str, str, str, float]] = [
            (item.id, item.kind.value, item.sensitivity.value, score)
            for score, item in pre_dedup_scored
        ]
    scored, dedup_removed = deduplicate_candidates(
        scored, similarity_threshold=manager._scoring.dedup_threshold
    )
    post_dedup_ids = {item.id for _score, item in scored}
    dedup_dropped_items = [
        item for _score, item in pre_dedup_scored if item.id not in post_dedup_ids
    ]
    if explain:
        dedup_dropped_records: list[tuple[str, str, str, float]] = [
            (iid, kind, sens, sc)
            for (iid, kind, sens, sc) in pre_dedup_view
            if iid not in post_dedup_ids
        ]
    else:
        dedup_dropped_records = []

    # Pre-build episodic + fact header so its token cost is reserved from the
    # budget *before* selection.  A caller-owned renderer (issue #410) owns the
    # whole layout, so header assembly is skipped and selection gets the full
    # phase budget.
    if renderer is None:
        full_header, hf_tokens = _assemble_header(manager, header, footer, phase)
        adjusted = adjust_budget_for_header(effective_budget, phase, hf_tokens)
    else:
        full_header, hf_tokens = "", 0
        adjusted = effective_budget

    # 7. Select (budget already accounts for header/footer overhead)
    selection = select_and_pack(scored, phase, adjusted, manager._policy, manager._estimator)
    selected = selection.selected
    dropped_records = [
        *(DroppedItem(item.id, "sensitivity") for item in sensitivity_dropped_items),
        *(DroppedItem(item.id, "dedup") for item in dedup_dropped_items),
        *(DroppedItem(item.id, reason) for item, reason in selection.dropped),
    ]
    dropped_reasons: dict[str, int] = {}
    for record in dropped_records:
        dropped_reasons[record.reason] = dropped_reasons.get(record.reason, 0) + 1
    stats = BuildStats(
        tokens_per_section=selection.tokens_per_section,
        total_candidates=total_candidates,
        included_count=len(selected),
        dropped_count=len(dropped_records),
        dropped_reasons=dropped_reasons,
        dropped_items=dropped_records,
        dedup_removed=dedup_removed,
        dependency_closures=closures,
        header_footer_tokens=hf_tokens,
        token_estimator=estimator_name(manager._estimator),
    )
    # Surface per-item firewall diagnostics (issue #402): one FirewallStats per
    # offloaded tool result.  Read ``stats.firewall_summary()`` for the roll-up.
    stats.firewall_events = [
        env.firewall_stats for env in envelopes if env.firewall_stats is not None
    ]
    if sensitivity_dropped_items:
        manager._hook.on_items_excluded(sensitivity_dropped_items, "sensitivity")
    if dedup_dropped_items:
        manager._hook.on_items_excluded(dedup_dropped_items, "dedup")
    for reason in ("kind_limit", "budget"):
        excluded = [item for item, drop_reason in selection.dropped if drop_reason == reason]
        if excluded:
            manager._hook.on_items_excluded(excluded, reason)
    active_budget = effective_budget.for_phase(phase)
    if hf_tokens > active_budget:
        manager._hook.on_budget_exceeded(hf_tokens, active_budget)
    elif selection.budget_overruns:
        requested, limit = max(selection.budget_overruns)
        manager._hook.on_budget_exceeded(requested, limit)

    if stats.included_count + stats.dropped_count != stats.total_candidates:
        raise ContextWeaverError(
            "context build accounting invariant failed: "
            f"included={stats.included_count} dropped={stats.dropped_count} "
            f"total={stats.total_candidates}"
        )

    # Budget-overflow policy (issue #510): warn/raise on budget drops when
    # configured; default "drop" is a no-op.  Runs after stats so a raise can
    # attach the would-be stats; accounting is identical in every mode.
    budget_dropped = [item for item, reason in selection.dropped if reason == "budget"]
    enforce_overflow_policy(stats, manager._policy, budget_dropped)

    # 8. Render (caller-owned renderer wins; default = section renderer)
    prompt = render_pack_prompt(selected, full_header=full_header, footer=footer, renderer=renderer)
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
            resolved_weights={
                "recency_weight": effective_scoring.recency_weight,
                "tag_match_weight": effective_scoring.tag_match_weight,
                "kind_priority_weight": effective_scoring.kind_priority_weight,
                "token_cost_penalty": effective_scoring.token_cost_penalty,
            },
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


def _classify_items(
    items: list[ContextItem], classifier: SensitivityClassifier
) -> list[ContextItem]:
    """Raise each item's sensitivity per *classifier* (issue #542); never lower it.

    Returns a new list; an item is only copied when its label actually changes,
    keeping the no-op case allocation-free.  The pipeline takes the maximum of
    the classifier's result and the item's current label as a safety net so a
    misbehaving classifier can never weaken enforcement.

    Every raise records ``metadata["sensitivity_raised_by"]`` (the classifier's
    type name) so the decision is auditable (issue #542 acceptance criterion):
    enforcement can later show *why* an item carried a higher label than the
    caller supplied.
    """
    raised_by = type(classifier).__name__
    out: list[ContextItem] = []
    for item in items:
        level = classifier.classify(item)
        if _SENSITIVITY_ORDER[level] > _SENSITIVITY_ORDER[item.sensitivity]:
            metadata = dict(item.metadata)
            metadata["sensitivity_raised_by"] = raised_by
            out.append(replace(item, sensitivity=level, metadata=metadata))
        else:
            out.append(item)
    return out


def _enforce_header_memory(
    entries: list[tuple[str, str, Sensitivity]],
    manager: _ManagerState,
) -> list[str]:
    """Filter header memory *entries* through the sensitivity layer (issue #450).

    Each entry is ``(item_id, rendered_text, sensitivity)``.  The entries are
    wrapped as synthetic ``memory_fact`` :class:`ContextItem` objects, optionally
    re-classified (issue #542), then passed through
    :func:`~contextweaver.context.sensitivity.apply_sensitivity_filter` so header
    content gets the *same* floor/redaction enforcement as pipeline-selected
    items.  Returns the surviving rendered texts (redacted items render their
    mask placeholder); dropped items are omitted entirely.
    """
    synthetic = [
        ContextItem(id=item_id, kind=ItemKind.memory_fact, text=text, sensitivity=sensitivity)
        for item_id, text, sensitivity in entries
    ]
    if manager._sensitivity_classifier is not None:
        synthetic = _classify_items(synthetic, manager._sensitivity_classifier)
    kept, _dropped = apply_sensitivity_filter(synthetic, manager._policy, manager._estimator)
    return [item.text for item in kept]


def _episode_sensitivity(manager: _ManagerState, episode_id: str) -> Sensitivity:
    """Return the stored sensitivity for *episode_id* (issue #450).

    ``EpisodicStore.latest`` yields ``(id, summary, metadata)`` tuples without the
    sensitivity field, so the full :class:`~contextweaver.store.episodic.Episode`
    is fetched via :meth:`EpisodicStore.get`; a missing episode (e.g. a concurrent
    delete) conservatively falls back to ``public``.
    """
    episode = manager._episodic_store.get(episode_id)
    return episode.sensitivity if episode is not None else Sensitivity.public


def _memory_allowed_in_phase(policy: ContextPolicy, phase: Phase) -> bool:
    """Return ``True`` when ``memory_fact`` content is permitted in *phase* (issue #450).

    Header facts and episode summaries are memory content; gating them on the
    ``memory_fact`` kind restores phase-policy consistency — a phase that
    excludes ``memory_fact`` from the pipeline no longer receives the same
    content through the header side-channel.
    """
    return ItemKind.memory_fact in policy.allowed_kinds_per_phase.get(phase, [])


def _assemble_header(
    manager: _ManagerState, header: str, footer: str, phase: Phase
) -> tuple[str, int]:
    """Build the full prompt header (episodic + facts + caller header).

    Episodic summaries and facts are routed through the sensitivity floor and
    the per-phase kind policy (issue #450) so the header surface carries the same
    guarantees as the pipeline surface.

    Returns ``(full_header, header_footer_token_estimate)``.
    """
    extra_sections: list[str] = []
    memory_allowed = _memory_allowed_in_phase(manager._policy, phase)

    # Episodic summaries (latest 3) — gated on the memory_fact phase policy and
    # filtered through the sensitivity layer.
    episodic_entries = manager._episodic_store.latest(3) if memory_allowed else []
    if episodic_entries:
        kept_summaries = _enforce_header_memory(
            [
                (f"episode:{ep_id}", ep_summary, _episode_sensitivity(manager, ep_id))
                for ep_id, ep_summary, _meta in episodic_entries
            ],
            manager,
        )
        if kept_summaries:
            ep_lines = ["[EPISODIC MEMORY]", *(f"- {summary}" for summary in kept_summaries)]
            extra_sections.append("\n".join(ep_lines))

    # Facts snapshot — gated on the memory_fact phase policy, filtered through
    # the sensitivity layer, then capped to avoid unbounded prompt growth.
    all_facts = manager._fact_store.all() if memory_allowed else []
    if all_facts:
        kept_fact_texts = _enforce_header_memory(
            [(fact.fact_id, f"{fact.key}: {fact.value}", fact.sensitivity) for fact in all_facts],
            manager,
        )
        if kept_fact_texts:
            fact_lines: list[str] = ["[FACTS]"]
            total_chars = len(fact_lines[0])
            for idx, text in enumerate(kept_fact_texts):
                if idx >= _MAX_FACT_LINES:
                    remaining = len(kept_fact_texts) - idx
                    if remaining > 0:
                        fact_lines.append(f"- ... ({remaining} more facts omitted)")
                    break
                line = f"- {text}"
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

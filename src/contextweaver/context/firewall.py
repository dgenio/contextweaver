"""Context firewall for contextweaver.

The firewall intercepts raw tool outputs before they reach the LLM context.
It replaces the raw text with a :class:`~contextweaver.types.ResultEnvelope`
containing a human-readable summary, extracted facts, and an
:class:`~contextweaver.types.ArtifactRef` to the out-of-band artifact store.
"""

from __future__ import annotations

import logging
from typing import Literal

from contextweaver.context.views import ViewRegistry, generate_views
from contextweaver.envelope import ResultEnvelope
from contextweaver.protocols import ArtifactStore, EventHook, Extractor, NoOpHook, Summarizer
from contextweaver.summarize.extract import extract_facts
from contextweaver.types import ContextItem, ItemKind

logger = logging.getLogger("contextweaver.context")


def _default_summary(raw: str, max_chars: int = 500) -> str:
    """Return a truncated first-paragraph summary of *raw*."""
    first_para = raw.split("\n\n")[0].strip()
    if len(first_para) > max_chars:
        return first_para[:max_chars] + "…"
    return first_para


def apply_firewall(
    item: ContextItem,
    artifact_store: ArtifactStore,
    hook: EventHook | None = None,
    view_registry: ViewRegistry | None = None,
    summarizer: Summarizer | None = None,
    extractor: Extractor | None = None,
) -> tuple[ContextItem, ResultEnvelope | None]:
    """Intercept a ``tool_result`` item and store its content out-of-band.

    For non-``tool_result`` items the function is a no-op and returns the
    original item unchanged.

    Args:
        item: The candidate item to inspect.
        artifact_store: Where to store the raw content.
        hook: Optional lifecycle hook to notify on firewall trigger.
        view_registry: Optional custom view registry for auto-view generation.
        summarizer: Optional :class:`~contextweaver.protocols.Summarizer`
            implementation.  When provided it replaces the built-in
            ``_default_summary`` heuristic.
        extractor: Optional :class:`~contextweaver.protocols.Extractor`
            implementation.  When provided it replaces the built-in
            :func:`~contextweaver.summarize.extract.extract_facts` call.

    Returns:
        A 2-tuple ``(processed_item, envelope_or_none)``.  When the firewall
        fires, *processed_item* has its ``text`` replaced with the summary and
        ``artifact_ref`` populated; *envelope_or_none* is a
        :class:`~contextweaver.types.ResultEnvelope`.  When no interception
        occurs, the original *item* is returned with ``None`` as the second
        element.
    """
    _hook = hook or NoOpHook()

    if item.kind != ItemKind.tool_result:
        return item, None

    raw_bytes = item.text.encode("utf-8")
    handle = f"artifact:{item.id}"
    media = str(item.metadata.get("media_type", "text/plain"))
    ref = artifact_store.put(
        handle=handle,
        content=raw_bytes,
        media_type=media,
        label=f"raw tool result for {item.id}",
    )

    status: Literal["ok", "partial", "error"] = "ok"
    try:
        if summarizer is not None:
            summary = summarizer.summarize(item.text, dict(item.metadata))
        else:
            summary = _default_summary(item.text)
    except Exception:  # noqa: BLE001
        summary = "(summary unavailable)"
        status = "error"

    try:
        if extractor is not None:
            facts = extractor.extract(item.text, dict(item.metadata))
        else:
            facts = extract_facts(item.text, item.metadata)
    except Exception:  # noqa: BLE001
        facts = []
        status = "error" if status == "error" else "partial"

    views = generate_views(ref, raw_bytes, registry=view_registry)

    envelope = ResultEnvelope(
        status=status,
        summary=summary,
        facts=facts,
        artifacts=[ref],
        views=views,
        provenance={"source_item_id": item.id},
    )

    processed = ContextItem(
        id=item.id,
        kind=item.kind,
        text=summary,
        token_estimate=len(summary) // 4,
        metadata=dict(item.metadata),
        parent_id=item.parent_id,
        artifact_ref=ref,
    )

    _hook.on_firewall_triggered(item, "tool_result intercepted")
    logger.debug("firewall: intercepted item_id=%s, summary_len=%d", item.id, len(summary))
    return processed, envelope


def apply_firewall_to_batch(
    items: list[ContextItem],
    artifact_store: ArtifactStore,
    hook: EventHook | None = None,
    view_registry: ViewRegistry | None = None,
    summarizer: Summarizer | None = None,
    extractor: Extractor | None = None,
) -> tuple[list[ContextItem], list[ResultEnvelope]]:
    """Apply the firewall to a list of items.

    Args:
        items: Candidate items (may contain a mix of kinds).
        artifact_store: Where to store raw tool outputs.
        hook: Optional lifecycle hook.
        view_registry: Optional custom view registry for auto-view generation.
        summarizer: Optional :class:`~contextweaver.protocols.Summarizer`
            passed through to each :func:`apply_firewall` call.
        extractor: Optional :class:`~contextweaver.protocols.Extractor`
            passed through to each :func:`apply_firewall` call.

    Returns:
        A 2-tuple of ``(processed_items, envelopes)``.
    """
    processed = []
    envelopes = []
    for item in items:
        p, env = apply_firewall(item, artifact_store, hook, view_registry, summarizer, extractor)
        processed.append(p)
        if env is not None:
            envelopes.append(env)
    logger.debug(
        "firewall_batch: processed=%d, intercepted=%d",
        len(processed),
        len(envelopes),
    )
    return processed, envelopes

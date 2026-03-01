"""Context firewall for contextweaver.

The firewall intercepts raw tool outputs before they reach the LLM context.
It replaces the raw text with a :class:`~contextweaver.types.ResultEnvelope`
containing a human-readable summary, extracted facts, and an
:class:`~contextweaver.types.ArtifactRef` to the out-of-band artifact store.
"""

from __future__ import annotations

from typing import Literal

from contextweaver.envelope import ResultEnvelope
from contextweaver.protocols import ArtifactStore, EventHook, NoOpHook
from contextweaver.summarize.extract import extract_facts
from contextweaver.types import ContextItem, ItemKind


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
) -> tuple[ContextItem, ResultEnvelope | None]:
    """Intercept a ``tool_result`` item and store its content out-of-band.

    For non-``tool_result`` items the function is a no-op and returns the
    original item unchanged.

    Args:
        item: The candidate item to inspect.
        artifact_store: Where to store the raw content.
        hook: Optional lifecycle hook to notify on firewall trigger.

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
    ref = artifact_store.put(
        handle=handle,
        content=raw_bytes,
        media_type="text/plain",
        label=f"raw tool result for {item.id}",
    )

    status: Literal["ok", "partial", "error"] = "ok"
    try:
        summary = _default_summary(item.text)
    except Exception:  # noqa: BLE001
        summary = "(summary unavailable)"
        status = "error"

    try:
        facts = extract_facts(item.text, item.metadata)
    except Exception:  # noqa: BLE001
        facts = []
        status = "error" if status == "error" else "partial"

    envelope = ResultEnvelope(
        status=status,
        summary=summary,
        facts=facts,
        artifacts=[ref],
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
    return processed, envelope


def apply_firewall_to_batch(
    items: list[ContextItem],
    artifact_store: ArtifactStore,
    hook: EventHook | None = None,
) -> tuple[list[ContextItem], list[ResultEnvelope]]:
    """Apply the firewall to a list of items.

    Args:
        items: Candidate items (may contain a mix of kinds).
        artifact_store: Where to store raw tool outputs.
        hook: Optional lifecycle hook.

    Returns:
        A 2-tuple of ``(processed_items, envelopes)``.
    """
    processed = []
    envelopes = []
    for item in items:
        p, env = apply_firewall(item, artifact_store, hook)
        processed.append(p)
        if env is not None:
            envelopes.append(env)
    return processed, envelopes

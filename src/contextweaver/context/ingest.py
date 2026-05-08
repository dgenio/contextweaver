"""Tool-result ingestion helpers.

Extracted from :mod:`contextweaver.context.manager` so that ``manager.py``
stays under the project's <=300 lines per module guideline (see AGENTS.md).
:class:`~contextweaver.context.manager.ContextManager` keeps the public
``ingest*`` methods as thin delegations; this module is not part of the
public API.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from contextweaver.context.firewall import apply_firewall
from contextweaver.context.views import ViewRegistry, generate_views
from contextweaver.envelope import ResultEnvelope
from contextweaver.protocols import (
    ArtifactStore,
    EventHook,
    EventLog,
    Extractor,
    Summarizer,
    TokenEstimator,
)
from contextweaver.types import ArtifactRef, ContextItem, ItemKind

logger = logging.getLogger(__name__)


def ingest_item(event_log: EventLog, item: ContextItem) -> None:
    """Append *item* to *event_log* and emit a debug log line."""
    event_log.append(item)
    logger.debug("ingest: item_id=%s, kind=%s", item.id, item.kind.value)


def ingest_tool_result(
    *,
    event_log: EventLog,
    artifact_store: ArtifactStore,
    hook: EventHook,
    view_registry: ViewRegistry,
    summarizer: Summarizer | None,
    extractor: Extractor | None,
    estimator: TokenEstimator,
    tool_call_id: str,
    raw_output: str,
    tool_name: str = "",
    media_type: str = "text/plain",
    firewall_threshold: int = 2000,
) -> tuple[ContextItem, ResultEnvelope]:
    """Ingest a raw tool result via :meth:`ContextManager.ingest_tool_result` logic."""
    item = ContextItem(
        id=f"result:{tool_call_id}",
        kind=ItemKind.tool_result,
        text=raw_output,
        token_estimate=estimator.estimate(raw_output),
        metadata={"tool_name": tool_name, "media_type": media_type},
        parent_id=tool_call_id,
    )

    if len(raw_output) > firewall_threshold:
        processed, envelope = apply_firewall(
            item,
            artifact_store,
            hook=hook,
            view_registry=view_registry,
            summarizer=summarizer,
            extractor=extractor,
        )
        if envelope is None:
            # Shouldn't happen for tool_result items, but be safe
            envelope = ResultEnvelope(status="ok", summary=raw_output[:500])
        event_log.append(processed)
        logger.debug(
            "ingest_tool_result: item_id=%s, firewall=True, output_len=%d",
            processed.id,
            len(raw_output),
        )
        return processed, envelope

    # Small output: extract facts and store in artifact store to enable drilldown
    from contextweaver.summarize.extract import extract_facts

    status: Literal["ok", "partial"] = "ok"
    try:
        facts = (
            extractor.extract(raw_output, item.metadata)
            if extractor is not None
            else extract_facts(raw_output, item.metadata)
        )
    except Exception:  # noqa: BLE001
        facts = []
        status = "partial"
    # For small outputs, store in artifact store to enable drilldown
    raw_bytes = raw_output.encode("utf-8")
    handle = f"artifact:{item.id}"
    ref = artifact_store.put(
        handle=handle,
        content=raw_bytes,
        media_type=media_type,
        label=f"raw tool result for {item.id}",
    )
    views = generate_views(ref, raw_bytes, registry=view_registry)
    envelope = ResultEnvelope(
        status=status,
        summary=raw_output,
        facts=facts,
        artifacts=[ref],
        views=views,
        provenance={"source_item_id": item.id, "tool_name": tool_name},
    )
    item = ContextItem(
        id=item.id,
        kind=item.kind,
        text=item.text,
        token_estimate=item.token_estimate,
        metadata=dict(item.metadata),
        parent_id=item.parent_id,
        artifact_ref=ref,
    )
    event_log.append(item)
    logger.debug(
        "ingest_tool_result: item_id=%s, firewall=False, output_len=%d",
        item.id,
        len(raw_output),
    )
    return item, envelope


def ingest_mcp_result(
    *,
    event_log: EventLog,
    artifact_store: ArtifactStore,
    hook: EventHook,
    summarizer: Summarizer | None,
    extractor: Extractor | None,
    estimator: TokenEstimator,
    tool_call_id: str,
    mcp_result: dict[str, Any],
    tool_name: str,
    firewall_threshold: int = 2000,
) -> tuple[ContextItem, ResultEnvelope]:
    """Ingest an MCP result via :meth:`ContextManager.ingest_mcp_result` logic."""
    from contextweaver.adapters.mcp import mcp_result_to_envelope

    envelope, binaries, full_text = mcp_result_to_envelope(mcp_result, tool_name)

    # Persist binary artifacts (images, resources) and refresh envelope metadata
    stored_refs: dict[str, ArtifactRef] = {}
    for handle, (raw_bytes, media_type, label) in sorted(binaries.items()):
        stored_refs[handle] = artifact_store.put(handle, raw_bytes, media_type, label)

    if stored_refs:
        # NOTE: intentional post-construction mutation — refresh refs with
        # store-canonical metadata (size_bytes, etc.).  Must be revisited
        # if ResultEnvelope is ever made frozen.
        envelope.artifacts = [stored_refs.get(a.handle, a) for a in envelope.artifacts]

    # Build the context item from the full raw text so the firewall
    # can offload the complete output, not the truncated summary.
    item = ContextItem(
        id=f"result:{tool_call_id}",
        kind=ItemKind.tool_result,
        text=full_text,
        token_estimate=estimator.estimate(full_text),
        metadata={"tool_name": tool_name, "protocol": "mcp"},
        parent_id=tool_call_id,
    )

    # Apply firewall if full text is large
    if len(full_text) > firewall_threshold:
        processed, fw_envelope = apply_firewall(
            item,
            artifact_store,
            hook=hook,
            view_registry=None,
            summarizer=summarizer,
            extractor=extractor,
        )
        if fw_envelope is not None:
            # Merge: keep MCP artifacts, use firewall summary/facts, preserve views
            envelope = ResultEnvelope(
                status=envelope.status,
                summary=fw_envelope.summary,
                facts=list(fw_envelope.facts) + list(envelope.facts),
                artifacts=list(envelope.artifacts) + list(fw_envelope.artifacts),
                provenance=envelope.provenance,
                views=list(envelope.views) + list(fw_envelope.views),
            )
        item = processed

    event_log.append(item)
    return item, envelope

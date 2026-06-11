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

from contextweaver.context.firewall import _extractor_is_llm, apply_firewall
from contextweaver.context.views import ViewRegistry, generate_views
from contextweaver.envelope import FirewallStats, ResultEnvelope
from contextweaver.exceptions import DeterminismError
from contextweaver.protocols import (
    ArtifactStore,
    EventHook,
    EventLog,
    Extractor,
    Summarizer,
    TokenEstimator,
)
from contextweaver.secrets import scrub_secrets, scrub_secrets_in_list
from contextweaver.summarize.structured import StructuredFirewall
from contextweaver.tokens import count as count_tokens
from contextweaver.types import ArtifactRef, ContextItem, ItemKind

logger = logging.getLogger(__name__)


def ingest_item(event_log: EventLog, item: ContextItem) -> None:
    """Append *item* to *event_log* and emit a debug log line."""
    event_log.append(item)
    logger.debug("ingest: item_id=%s, kind=%s", item.id, item.kind.value)


def drilldown(
    *,
    artifact_store: ArtifactStore,
    event_log: EventLog,
    estimator: TokenEstimator,
    handle: str,
    selector: dict[str, Any],
    inject: bool = False,
    parent_id: str | None = None,
) -> str:
    """Fetch a slice of a stored artifact, optionally injecting it (issue #101).

    Implements :meth:`ContextManager.drilldown`; when *inject* is ``True`` the
    slice is appended to *event_log* as a ``tool_result`` for later builds.
    """
    result = artifact_store.drilldown(handle, selector)
    if inject:
        sel_type = selector.get("type", "unknown")
        item_id = f"drilldown:{handle}:{sel_type}:{event_log.count()}"
        event_log.append(
            ContextItem(
                id=item_id,
                kind=ItemKind.tool_result,
                text=result,
                token_estimate=estimator.estimate(result),
                metadata={"drilldown_handle": handle, "selector": selector},
                parent_id=parent_id,
            )
        )
    return result


def ingest_envelope(
    *,
    event_log: EventLog,
    estimator: TokenEstimator,
    tool_call_id: str,
    envelope: ResultEnvelope,
    tool_name: str = "",
) -> ContextItem:
    """Ingest an already-firewalled :class:`ResultEnvelope` (canonical seam).

    This is the canonical, Frame-shaped ingestion path (weaver-spec I-05): the
    execution boundary firewalls raw output and hands contextweaver a
    :class:`ResultEnvelope` (the native preimage of a weaver-spec ``Frame``).
    contextweaver appends a summary-only :class:`ContextItem` carrying the
    envelope's artifact handle and does **not** re-derive firewalling from raw
    output. Contrast with :func:`ingest_tool_result` / :func:`ingest_mcp_result`,
    which accept raw output and run the firewall themselves.
    """
    ref = envelope.artifacts[0] if envelope.artifacts else None
    item = ContextItem(
        id=f"result:{tool_call_id}",
        kind=ItemKind.tool_result,
        text=envelope.summary,
        token_estimate=estimator.estimate(envelope.summary),
        metadata={"tool_name": tool_name, "ingest": "envelope"},
        parent_id=tool_call_id,
        artifact_ref=ref,
    )
    event_log.append(item)
    logger.debug(
        "ingest_envelope: item_id=%s, has_artifact=%s, summary_len=%d",
        item.id,
        ref is not None,
        len(envelope.summary),
    )
    return item


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
    deterministic: bool = False,
    redact_secrets: bool = False,
    firewall: StructuredFirewall | None = None,
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
            deterministic=deterministic,
            keep=firewall.keep if firewall is not None else None,
            threshold_chars=firewall_threshold,
            redact_secrets=redact_secrets,
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

    # Issue #461 — the deterministic guarantee must hold on the small-output
    # path too: an LLM-backed extractor would otherwise route this result
    # through a model even though no firewall summarisation fires here.
    if deterministic and _extractor_is_llm(extractor):
        raise DeterminismError(
            f"deterministic=True but an LLM-backed extractor would process item "
            f"{item.id!r}; refusing to pass data through a model. Supply a "
            f"rule-based extractor instead."
        )

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
    # Issue #428 — for a sub-threshold result the raw text *is* the prompt-bound
    # surface (the firewall does not fire), so scrub the summary, facts, and the
    # item text itself.  The out-of-band raw artifact below is left intact.
    summary_text = raw_output
    item_text = item.text
    if redact_secrets:
        summary_text = scrub_secrets(summary_text)
        facts = scrub_secrets_in_list(facts)
        item_text = scrub_secrets(item_text)
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
    _tokens = count_tokens(summary_text)
    envelope = ResultEnvelope(
        status=status,
        summary=summary_text,
        facts=facts,
        artifacts=[ref],
        views=views,
        provenance={"source_item_id": item.id, "tool_name": tool_name},
        firewall_stats=FirewallStats(
            triggered=False,
            strategy="passthrough",
            threshold_chars=firewall_threshold,
            original_chars=len(raw_output),
            original_tokens=_tokens,
            summary_chars=len(summary_text),
            summary_tokens=_tokens,
            artifact_ref=ref.handle,
        ),
    )
    item = ContextItem(
        id=item.id,
        kind=item.kind,
        text=item_text,
        token_estimate=estimator.estimate(item_text) if redact_secrets else item.token_estimate,
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
    deterministic: bool = False,
    redact_secrets: bool = False,
    firewall: StructuredFirewall | None = None,
    view_registry: ViewRegistry | None = None,
) -> tuple[ContextItem, ResultEnvelope]:
    """Ingest an MCP result via :meth:`ContextManager.ingest_mcp_result` logic.

    *view_registry* is threaded into the firewall so custom view generators
    registered on the manager fire on this path too (issue #460); ``None`` uses
    the default registry.
    """
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
            view_registry=view_registry,
            summarizer=summarizer,
            extractor=extractor,
            deterministic=deterministic,
            keep=firewall.keep if firewall is not None else None,
            threshold_chars=firewall_threshold,
            redact_secrets=redact_secrets,
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
                firewall_stats=fw_envelope.firewall_stats,
            )
        item = processed

    event_log.append(item)
    return item, envelope

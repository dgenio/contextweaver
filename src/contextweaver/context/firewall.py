"""Context firewall for contextweaver.

Core firewall logic extracted for testability.
"""

from __future__ import annotations

from contextweaver.protocols import Extractor, Summarizer, TokenEstimator
from contextweaver.store.artifacts import InMemoryArtifactStore
from contextweaver.types import ArtifactRef, ContextItem, ItemKind, ResultEnvelope, ViewSpec


async def apply_firewall(
    raw_output: str | bytes,
    tool_call_id: str,
    tool_name: str,
    media_type: str,
    artifact_store: InMemoryArtifactStore,
    summarizer: Summarizer,
    extractor: Extractor,
    token_estimator: TokenEstimator,
    firewall_threshold: int,
) -> tuple[ContextItem, ResultEnvelope]:
    """Core firewall logic.

    If len(raw_output) > firewall_threshold:
      1. Store raw in artifact_store -> handle
      2. Summarize via summarizer -> summary text
      3. Extract structured info via extractor -> facts dict
      4. Create ContextItem with text=summary, artifact_ref=handle
      5. Create ResultEnvelope
    If small:
      - ContextItem.text = raw_output
      - Still extract structured info -> ResultEnvelope.facts
    """
    text = (
        raw_output if isinstance(raw_output, str) else raw_output.decode("utf-8", errors="replace")
    )
    size = len(text)

    if size > firewall_threshold:
        handle = f"art_{tool_call_id}"
        await artifact_store.put(
            handle,
            raw_output,
            metadata={
                "tool_name": tool_name,
                "media_type": media_type,
                "original_size": size,
            },
        )

        summary = summarizer.summarize(text)
        facts = extractor.extract(text, media_type)

        item = ContextItem(
            id=f"tr_{tool_call_id}",
            kind=ItemKind.TOOL_RESULT,
            text=summary,
            token_estimate=token_estimator.estimate(summary),
            metadata={"tool_name": tool_name, "media_type": media_type, "original_size": size},
            parent_id=tool_call_id,
            artifact_ref=handle,
        )

        artifact_ref = ArtifactRef(
            handle=handle,
            media_type=media_type,
            size_bytes=size,
            label=f"Raw output from {tool_name}",
        )

        views = [
            ViewSpec(
                view_id=f"head_{handle}",
                label="First 500 chars",
                selector={"type": "head", "chars": 500},
                artifact_ref=handle,
            ),
        ]

        envelope = ResultEnvelope(
            status="ok",
            summary=summary,
            facts=facts,
            artifacts=[artifact_ref],
            views=views,
            provenance={"tool_name": tool_name, "tool_call_id": tool_call_id},
        )
    else:
        facts = extractor.extract(text, media_type)

        item = ContextItem(
            id=f"tr_{tool_call_id}",
            kind=ItemKind.TOOL_RESULT,
            text=text,
            token_estimate=token_estimator.estimate(text),
            metadata={"tool_name": tool_name, "media_type": media_type},
            parent_id=tool_call_id,
        )

        envelope = ResultEnvelope(
            status="ok",
            summary=text,
            facts=facts,
            provenance={"tool_name": tool_name, "tool_call_id": tool_call_id},
        )

    return item, envelope

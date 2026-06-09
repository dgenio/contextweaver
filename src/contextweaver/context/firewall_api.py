"""Single-call context-firewall facade (issues #399, #403).

The canonical firewall path goes through ``ContextManager.ingest_* ->
build``, which is the right tool when you are compiling a *whole* phase prompt.
But the most common integration question is narrower:

    "I have one large tool-result dict/list — how do I shrink it before it
    enters the LLM prompt, without rewriting my tool layer?"

:func:`compact_tool_result` answers exactly that in one call.  It composes the
firewall primitives:

- deterministic **structured** field projection (issue #406) when you pass a
  ``keep`` allow-list, or rule-based/LLM text summarisation otherwise;
- a **schema-preserving pass-through** (issue #403): when the payload is under
  threshold the original shape is returned unchanged, with firewall metadata
  attached only on a reserved namespaced ``_cw`` sidecar key — never an
  in-place rewrite of the caller's fields;
- the built-in **token counter** (issue #405) so reported savings match what
  callers measure;
- a fail-closed **deterministic** mode (issue #404, on by default here) so
  financial/regulated callers can guarantee no LLM touched the data.

Example::

    from contextweaver import compact_tool_result

    out = compact_tool_result(
        {"invoices": [...]},
        threshold_chars=2000,
        keep=["invoices[].invoiceNumber", "invoices[].amount", "invoices[].status"],
    )
    out.firewalled          # True
    out.payload             # projected subset + {"_cw": {...}} sidecar
    out.stats.tokens_saved  # how much stayed out of the prompt
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Literal

from contextweaver.envelope import FirewallStats
from contextweaver.exceptions import ConfigError
from contextweaver.protocols import ArtifactStore, Extractor, Summarizer
from contextweaver.store.artifacts import InMemoryArtifactStore
from contextweaver.tokens import count as count_tokens
from contextweaver.types import ContextItem, ItemKind

#: Reserved sidecar key for firewall metadata.  Chosen to be namespaced so it
#: will not collide with caller fields (issue #403).
CW_SIDECAR_KEY: str = "_cw"

Strategy = Literal["auto", "structured", "text", "passthrough"]


@dataclass
class CompactResult:
    """Result of :func:`compact_tool_result`.

    Attributes:
        firewalled: ``True`` when the payload was offloaded out-of-band;
            ``False`` for the under-threshold pass-through.
        payload: The object to hand to the LLM.  On pass-through this is the
            caller's payload shape-unchanged (plus a ``_cw`` sidecar when it is
            a dict).  When firewalled it is the projected subset (structured)
            or a ``{"_cw_summary", "_cw_artifact_ref", "_cw"}`` envelope (text).
        summary: The inline summary text, or ``None`` on pass-through.
        facts: Structured facts derived from the payload (may be empty).
        artifact_ref: Handle of the offloaded raw payload, or ``None``.
        stats: The :class:`~contextweaver.envelope.FirewallStats` for this call.
    """

    firewalled: bool
    payload: Any
    summary: str | None
    facts: list[str] = field(default_factory=list)
    artifact_ref: str | None = None
    stats: FirewallStats = field(
        default_factory=lambda: FirewallStats(triggered=False, strategy="noop")
    )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "firewalled": self.firewalled,
            "payload": self.payload,
            "summary": self.summary,
            "facts": list(self.facts),
            "artifact_ref": self.artifact_ref,
            "stats": self.stats.to_dict(),
        }


def _to_text(data: Any) -> tuple[str, str]:  # noqa: ANN401 — arbitrary JSON tool result
    """Return ``(text, media_type)`` for *data* (deterministic JSON for non-str)."""
    if isinstance(data, str):
        return data, "text/plain"
    return json.dumps(data, sort_keys=True, default=str), "application/json"


def _sidecar(stats: FirewallStats) -> dict[str, Any]:
    """Build the reserved ``_cw`` sidecar payload from *stats*."""
    return {
        "firewalled": stats.triggered,
        "strategy": stats.strategy,
        "artifact_ref": stats.artifact_ref,
        "original_chars": stats.original_chars,
        "summary_chars": stats.summary_chars,
        "summarized_by_llm": stats.summarized_by_llm,
    }


def compact_tool_result(
    data: dict[str, Any] | list[Any] | str,
    *,
    threshold_chars: int = 2000,
    budget: int = 800,
    strategy: Strategy = "auto",
    keep: list[str] | None = None,
    artifact_store: ArtifactStore | None = None,
    summarizer: Summarizer | None = None,
    extractor: Extractor | None = None,
    deterministic: bool = True,
    token_model: str | None = None,
) -> CompactResult:
    """Compact a single tool result in one call (firewall-only pattern).

    Args:
        data: The tool result — a dict, list, or string.
        threshold_chars: Payloads at or below this size are passed through
            unchanged (issue #403); larger payloads are firewalled.
        budget: Soft token budget for the inline *text* summary.  When the text
            summary would exceed it the summary is truncated.  Ignored for the
            structured strategy (which is lossless within the allow-list).
        strategy: ``"auto"`` (structured when *keep* is given, else text),
            ``"structured"`` (requires *keep*), ``"text"`` (force summarisation),
            or ``"passthrough"`` (never offload; only attach the sidecar).
        keep: JSON path allow-list for structured projection (issue #406).
        artifact_store: Where to offload the raw payload.  A private
            :class:`~contextweaver.store.artifacts.InMemoryArtifactStore` is
            created when ``None``; pass one to retain handles for ``drilldown``.
        summarizer: Optional summariser for the text strategy.
        extractor: Optional fact extractor for the text strategy.
        deterministic: When ``True`` (default) the call fails closed rather than
            invoking an LLM-backed *summarizer* (issue #404).
        token_model: Optional model/encoding name for token counting (#405).

    Returns:
        A :class:`CompactResult`.

    Raises:
        ConfigError: If ``strategy="structured"`` without a non-empty *keep*.
        DeterminismError: If ``deterministic=True`` and the text strategy would
            invoke an LLM-backed summariser.
    """
    if strategy == "structured" and not keep:
        raise ConfigError("strategy='structured' requires a non-empty `keep` allow-list")

    text, media_type = _to_text(data)
    original_chars = len(text)
    original_tokens = count_tokens(text, model=token_model)

    # Under threshold → schema-preserving pass-through for every strategy
    # (issue #403); ``strategy="passthrough"`` forces it regardless of size.
    # To force a firewall on a small payload, pass ``threshold_chars=0``.
    passthrough = strategy == "passthrough" or original_chars <= threshold_chars

    if passthrough:
        stats = FirewallStats(
            triggered=False,
            strategy="passthrough",
            threshold_chars=threshold_chars,
            original_chars=original_chars,
            original_tokens=original_tokens,
            summary_chars=original_chars,
            summary_tokens=original_tokens,
        )
        # Shape-preserving: attach the sidecar only to dicts; lists/strings are
        # returned byte-identical so downstream field access never breaks (#403).
        if isinstance(data, dict):
            payload: Any = {**data, CW_SIDECAR_KEY: _sidecar(stats)}
        else:
            payload = data
        return CompactResult(
            firewalled=False, payload=payload, summary=None, artifact_ref=None, stats=stats
        )

    # Over threshold (or forced) — offload via the shared firewall primitive.
    store = artifact_store if artifact_store is not None else InMemoryArtifactStore()
    keep_for_call = keep if strategy in ("auto", "structured") else None
    item = ContextItem(
        id=f"compact:{original_chars}",
        kind=ItemKind.tool_result,
        text=text,
        metadata={"media_type": media_type},
    )
    from contextweaver.context.firewall import apply_firewall

    processed, envelope = apply_firewall(
        item,
        store,
        summarizer=summarizer,
        extractor=extractor,
        deterministic=deterministic,
        keep=keep_for_call,
        threshold_chars=threshold_chars,
    )
    assert envelope is not None  # tool_result always produces an envelope here
    stats = envelope.firewall_stats or FirewallStats(triggered=True, strategy="summary")
    handle = stats.artifact_ref
    summary = envelope.summary

    if stats.strategy == "structured":
        try:
            projected = json.loads(summary)
        except (json.JSONDecodeError, ValueError):
            projected = summary
        if isinstance(projected, dict):
            payload = {**projected, CW_SIDECAR_KEY: _sidecar(stats)}
        else:
            payload = {"_cw_data": projected, CW_SIDECAR_KEY: _sidecar(stats)}
    else:
        summary = _truncate_to_budget(summary, budget, token_model)
        stats.summary_chars = len(summary)
        stats.summary_tokens = count_tokens(summary, model=token_model)
        payload = {
            "_cw_summary": summary,
            "_cw_artifact_ref": handle,
            CW_SIDECAR_KEY: _sidecar(stats),
        }

    return CompactResult(
        firewalled=True,
        payload=payload,
        summary=summary,
        facts=list(envelope.facts),
        artifact_ref=handle,
        stats=stats,
    )


def _truncate_to_budget(summary: str, budget: int, token_model: str | None) -> str:
    """Truncate *summary* so its token count stays within *budget* (soft cap)."""
    if budget <= 0 or count_tokens(summary, model=token_model) <= budget:
        return summary
    # Approximate: ~4 chars/token, then trim until under budget.
    trimmed = summary[: max(budget * 4, 1)]
    while trimmed and count_tokens(trimmed, model=token_model) > budget:
        trimmed = trimmed[: max(len(trimmed) - 64, 0)]
    return trimmed + "…"


#: Alias matching the name suggested in issue #399.
firewalled_tool_result = compact_tool_result

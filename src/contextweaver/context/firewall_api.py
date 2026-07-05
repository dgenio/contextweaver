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

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any, Literal

from contextweaver.context._firewall_api_helpers import sidecar as _sidecar
from contextweaver.context._firewall_api_helpers import to_text as _to_text
from contextweaver.context._firewall_api_helpers import truncate_to_budget as _truncate_to_budget
from contextweaver.envelope import FirewallStats
from contextweaver.exceptions import ConfigError, ContextWeaverError
from contextweaver.protocols import ArtifactStore, Extractor, Summarizer
from contextweaver.secrets import scrub_secrets, scrub_secrets_in_obj
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
    overwrite_sidecar: bool = False,
    redact_secrets: bool = False,
) -> CompactResult:
    """Compact a single tool result in one call (firewall-only pattern).

    .. note::
       This facade defaults ``deterministic=True`` (fail-closed), whereas
       :class:`~contextweaver.context.manager.ContextManager` defaults
       ``deterministic=False``.  The facade targets one-shot regulated callers;
       the manager targets long-running agent loops that opt in explicitly.

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
        overwrite_sidecar: When the input *data* is a dict that already contains
            the reserved :data:`CW_SIDECAR_KEY` (``"_cw"``), the call raises
            ``ConfigError`` by default rather than silently clobbering it
            (reserved-namespace rule, issue #467).  Set ``True`` to opt into
            overwriting — useful when round-tripping prior contextweaver output
            back through the facade.
        redact_secrets: When ``True`` (issue #745) well-known secret shapes are
            scrubbed from every prompt-bound surface this call returns — the
            schema-preserving pass-through payload (string leaves only, shape
            unchanged), the inline text summary, and the projected structured
            payload — via :func:`~contextweaver.secrets.scrub_secrets`. Defaults
            to ``False`` (posture is owned by #744). The raw payload offloaded to
            the *artifact_store* is left intact (out-of-band, gated by ``drilldown``).

    Returns:
        A :class:`CompactResult`.

    Raises:
        ConfigError: If ``strategy="structured"`` without a non-empty *keep*;
            if ``strategy="structured"`` is given non-JSON data; or if *data*
            already carries the reserved ``_cw`` key and *overwrite_sidecar* is
            ``False``.
        DeterminismError: If ``deterministic=True`` and the text strategy would
            invoke an LLM-backed summariser.
    """
    if isinstance(data, dict) and CW_SIDECAR_KEY in data and not overwrite_sidecar:
        raise ConfigError(
            f"input payload already contains the reserved {CW_SIDECAR_KEY!r} sidecar key; "
            f"refusing to overwrite caller data (issue #467). Strip the key first, or pass "
            f"overwrite_sidecar=True to replace it."
        )
    if strategy == "structured" and not keep:
        raise ConfigError("strategy='structured' requires a non-empty `keep` allow-list")

    text, media_type = _to_text(data)
    if strategy == "structured":
        # Fail loud rather than silently downgrade to a text summary: structured
        # projection needs a JSON payload (the firewall's ``use_structured`` path
        # requires ``_looks_like_json`` + ``json.loads``).  ``"auto"`` may fall
        # back to text on non-JSON, but an *explicit* ``"structured"`` request
        # must not quietly become a summary.
        try:
            json.loads(text)
        except (json.JSONDecodeError, ValueError) as exc:
            raise ConfigError(
                "strategy='structured' requires JSON-parseable data (dict, list, "
                "or a JSON string); the given payload is not valid JSON. Use "
                "strategy='auto' or 'text' for free-form text."
            ) from exc
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
        # When redacting, scrub string leaves first (shape/keys unchanged, #745).
        body = scrub_secrets_in_obj(data) if redact_secrets else data
        if isinstance(body, dict):
            payload: Any = {**body, CW_SIDECAR_KEY: _sidecar(stats)}
        else:
            payload = body
        return CompactResult(
            firewalled=False, payload=payload, summary=None, artifact_ref=None, stats=stats
        )

    # Over threshold (or forced) — offload via the shared firewall primitive.
    store = artifact_store if artifact_store is not None else InMemoryArtifactStore()
    keep_for_call = keep if strategy in ("auto", "structured") else None
    # Content-derived id so the artifact handle (``artifact:{id}``) is unique
    # per payload — two same-length payloads must not collide in a shared
    # ArtifactStore.  A digest (not a UUID) keeps the id deterministic.
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
    item = ContextItem(
        id=f"compact:{digest}",
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
    if envelope is None:  # tool_result always produces an envelope here
        raise ContextWeaverError(
            "internal invariant violated: apply_firewall returned no envelope for a "
            "tool_result item"
        )
    stats = envelope.firewall_stats or FirewallStats(triggered=True, strategy="summary")
    handle = stats.artifact_ref
    summary = envelope.summary

    if stats.strategy == "structured":
        try:
            projected = json.loads(summary)
        except (json.JSONDecodeError, ValueError):
            projected = summary
        if redact_secrets:  # scrub prompt-bound projection; shape unchanged (#745)
            projected = scrub_secrets_in_obj(projected)
            summary = scrub_secrets(summary)
        if isinstance(projected, dict):
            payload = {**projected, CW_SIDECAR_KEY: _sidecar(stats)}
        else:
            payload = {"_cw_data": projected, CW_SIDECAR_KEY: _sidecar(stats)}
    else:
        if redact_secrets:  # scrub before truncation so no partial secret survives
            summary = scrub_secrets(summary)
        summary = _truncate_to_budget(summary, budget, token_model)
        stats.summary_chars = len(summary)
        stats.summary_tokens = count_tokens(summary, model=token_model)
        payload = {
            "_cw_summary": summary,
            "_cw_artifact_ref": handle,
            CW_SIDECAR_KEY: _sidecar(stats),
        }

    facts = list(envelope.facts)
    if redact_secrets:
        facts = [scrub_secrets(fact) for fact in facts]
    return CompactResult(
        firewalled=True,
        payload=payload,
        summary=summary,
        facts=facts,
        artifact_ref=handle,
        stats=stats,
    )


#: Alias matching the name suggested in issue #399.
firewalled_tool_result = compact_tool_result

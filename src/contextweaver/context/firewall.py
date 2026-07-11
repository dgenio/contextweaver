"""Context firewall for contextweaver.

The firewall intercepts raw tool outputs before they reach the LLM context.
It replaces the raw text with a :class:`~contextweaver.types.ResultEnvelope`
containing a human-readable summary, extracted facts, and an
:class:`~contextweaver.types.ArtifactRef` to the out-of-band artifact store.
"""

from __future__ import annotations

import json
import logging
from typing import Literal

from contextweaver.context.views import ViewRegistry, generate_views
from contextweaver.envelope import FirewallStats, ResultEnvelope
from contextweaver.exceptions import DeterminismError
from contextweaver.protocols import ArtifactStore, EventHook, Extractor, NoOpHook, Summarizer
from contextweaver.secrets import scrub_secrets, scrub_secrets_in_list
from contextweaver.summarize.extract import extract_facts
from contextweaver.summarize.structured import StructuredFirewall
from contextweaver.tokens import count as count_tokens
from contextweaver.tokens import heuristic_counter
from contextweaver.types import ContextItem, ItemKind

logger = logging.getLogger("contextweaver.context")

# Deterministic, env-independent counter for the firewalled item's budget
# estimate.  The savings metrics on ``FirewallStats`` use the exact (tiktoken)
# ``count_tokens`` above, but the item's ``token_estimate`` feeds budget
# selection and the deterministic demo/scorecard artifacts, so it must not vary
# with tiktoken-cache availability — the script-aware heuristic matches
# ``len // 4`` for ASCII and stays reproducible offline (issues #530/#525).
_HEURISTIC = heuristic_counter()


def _default_summary(raw: str, max_chars: int = 500) -> str:
    """Return a truncated first-paragraph summary of *raw*."""
    first_para = raw.split("\n\n")[0].strip()
    if len(first_para) > max_chars:
        return first_para[:max_chars] + "…"
    return first_para


def _looks_like_json(text: str) -> bool:
    """Return ``True`` when *text* plausibly starts a JSON object/array."""
    stripped = text.lstrip()
    return stripped.startswith(("{", "["))


def _summarizer_is_llm(summarizer: Summarizer | None) -> bool:
    """Return ``True`` when *summarizer* declares itself LLM-backed (issue #404).

    Convention: LLM-backed summarisers (e.g.
    :class:`~contextweaver.extras.llm_summarizer.LlmSummarizer`) set the
    class attribute ``is_llm = True``.  Deterministic rule-based summarisers do
    not, so the firewall can decide whether a path is model-free.
    """
    return summarizer is not None and bool(getattr(summarizer, "is_llm", False))


def _extractor_is_llm(extractor: Extractor | None) -> bool:
    """Return ``True`` when *extractor* declares itself LLM-backed (issue #461).

    Mirrors :func:`_summarizer_is_llm`: LLM-backed extractors (e.g.
    :class:`~contextweaver.extras.llm_summarizer.LlmExtractor`) set
    ``is_llm = True``.  The firewall's ``deterministic=True`` mode must gate the
    extractor too — otherwise a configured LLM-backed extractor would route every
    tool result through a model even though the mode promises that no data was
    passed through one.
    """
    return extractor is not None and bool(getattr(extractor, "is_llm", False))


def apply_firewall(
    item: ContextItem,
    artifact_store: ArtifactStore,
    hook: EventHook | None = None,
    view_registry: ViewRegistry | None = None,
    summarizer: Summarizer | None = None,
    extractor: Extractor | None = None,
    *,
    deterministic: bool = False,
    keep: list[str] | None = None,
    threshold_chars: int = 0,
    redact_secrets: bool = False,
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
        deterministic: When ``True`` (issue #404) the firewall *fails closed* —
            if the chosen path would invoke an LLM-backed *summarizer* it raises
            :class:`~contextweaver.exceptions.DeterminismError` instead of
            calling the model.  Structured projection and the rule-based
            summary are always model-free and unaffected.
        keep: Optional JSON path allow-list (issue #406).  When supplied and the
            payload parses as JSON, the firewall uses lossless field projection
            (``strategy="structured"``) instead of text summarisation; the full
            payload is still offloaded so dropped fields stay retrievable via
            ``drilldown``.
        threshold_chars: The character threshold the caller compared against,
            recorded on :class:`~contextweaver.envelope.FirewallStats` for
            diagnostics.  Does not gate execution (the caller already decided to
            fire); ``0`` means "not recorded".
        redact_secrets: When ``True`` (issue #428) the prompt-bound summary and
            extracted facts are passed through the deterministic
            :func:`contextweaver.secrets.scrub_secrets` pass before they enter
            the :class:`~contextweaver.types.ResultEnvelope` and the processed
            item, so credential shapes copied from the raw payload never reach
            the prompt.  The out-of-band raw artifact is unchanged.

    Returns:
        A 2-tuple ``(processed_item, envelope_or_none)``.  When the firewall
        fires, *processed_item* has its ``text`` replaced with the summary and
        ``artifact_ref`` populated; *envelope_or_none* is a
        :class:`~contextweaver.types.ResultEnvelope` carrying a populated
        :attr:`~contextweaver.envelope.ResultEnvelope.firewall_stats`.  When no
        interception occurs, the original *item* is returned with ``None``.

    Raises:
        DeterminismError: If *deterministic* is ``True`` and the path would
            invoke an LLM-backed summariser.
    """
    _hook = hook or NoOpHook()

    if item.kind != ItemKind.tool_result:
        return item, None

    # Issue #352 — canonical-seam idempotency.  Items ingested via
    # ``ContextManager.ingest_envelope`` carry ``metadata["ingest"] ==
    # "envelope"``: their raw output was already firewalled upstream (the
    # execution boundary handed us a Frame/ResultEnvelope), ``text`` is the
    # upstream summary, and ``artifact_ref`` points at the upstream handle.
    # Re-firing here would store the *summary* as a new artifact and clobber
    # that handle, so short-circuit unconditionally — even when the upstream
    # ``ArtifactRef`` carries no ``content_hash`` (e.g. foreign Frames decoded
    # via ``from_weaver_frame``).
    if item.metadata.get("ingest") == "envelope":
        return item, None

    # Issue #190 — content-addressed firewall idempotency.  If the item
    # already carries an ``artifact_ref`` with a populated
    # ``content_hash``, an earlier firewall pass has stored the raw
    # bytes and replaced ``item.text`` with the summary.  Re-firing on a
    # subsequent ``build()`` call would overwrite the original raw bytes
    # with the summary and silently break drilldown.  Short-circuit: the
    # item is already firewall-processed; return it untouched and signal
    # "no new envelope" so downstream stages know nothing changed.
    if item.artifact_ref is not None and item.artifact_ref.content_hash:
        return item, None

    raw_bytes = item.text.encode("utf-8")
    handle = f"artifact:{item.id}"
    media = str(item.metadata.get("media_type", "text/plain"))
    # The store stamps ``content_hash`` (sha256 of the stored bytes) onto the
    # returned ref (#466).  Subsequent firewall passes use it to detect an
    # already-processed item and short-circuit (#190) — and because the store
    # now *persists* the hash, that idempotency survives a process restart when
    # the ref is reloaded from a ``JsonFileArtifactStore``.
    ref = artifact_store.put(
        handle=handle,
        content=raw_bytes,
        media_type=media,
        label=f"raw tool result for {item.id}",
    )

    # Choose a strategy.  Structured projection (issue #406) takes precedence
    # when an allow-list is supplied and the payload is JSON; it is always
    # model-free.  Otherwise summarise — LLM-backed only when the summariser
    # declares itself so.  ``bool(keep)`` (not ``keep is not None``): an empty
    # allow-list means "no structured projection requested", since a
    # StructuredFirewall(keep=[]) ConfigError would be swallowed below.
    use_structured = bool(keep) and _looks_like_json(item.text)
    summarized_by_llm = (not use_structured) and _summarizer_is_llm(summarizer)
    llm_provider = getattr(summarizer, "provider_metadata", None) if summarized_by_llm else None
    extracted_by_llm = (not use_structured) and _extractor_is_llm(extractor)
    if deterministic and (summarized_by_llm or extracted_by_llm):
        plugin = "summarizer" if summarized_by_llm else "extractor"
        raise DeterminismError(
            f"deterministic=True but an LLM-backed {plugin} would process item "
            f"{item.id!r}; refusing to pass data through a model. Supply a structured "
            f"`keep` allow-list or a rule-based summarizer/extractor instead."
        )

    status: Literal["ok", "partial", "error"] = "ok"
    facts: list[str]
    strategy: str

    if use_structured:
        strategy = "structured"
        try:
            projected, facts = StructuredFirewall(keep=list(keep or [])).compact(
                json.loads(item.text)
            )
            summary = json.dumps(projected, sort_keys=True)
        except Exception:  # noqa: BLE001 - malformed projection degrades safely
            strategy = "summary"
            summary = _default_summary(item.text)
            facts = []
            status = "partial"
    else:
        strategy = "llm_summary" if summarized_by_llm else "summary"
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

    # Issue #428 — scrub credential shapes from the prompt-bound surfaces
    # (summary + facts) before they leave the firewall.  Deterministic and
    # substring-level; the out-of-band raw artifact already stored above is
    # intentionally untouched (it stays retrievable only via drilldown).
    if redact_secrets:
        summary = scrub_secrets(summary)
        facts = scrub_secrets_in_list(facts)

    views = generate_views(ref, raw_bytes, registry=view_registry)

    firewall_stats = FirewallStats(
        triggered=True,
        strategy=strategy,
        threshold_chars=threshold_chars,
        original_chars=len(item.text),
        original_tokens=count_tokens(item.text),
        summary_chars=len(summary),
        summary_tokens=count_tokens(summary),
        artifact_ref=ref.handle,
        summarized_by_llm=summarized_by_llm,
        llm_provider=llm_provider,
    )

    envelope = ResultEnvelope(
        status=status,
        summary=summary,
        facts=facts,
        artifacts=[ref],
        views=views,
        provenance={"source_item_id": item.id},
        firewall_stats=firewall_stats,
    )

    processed = ContextItem(
        id=item.id,
        kind=item.kind,
        text=summary,
        token_estimate=_HEURISTIC.estimate(summary),
        metadata=dict(item.metadata),
        parent_id=item.parent_id,
        artifact_ref=ref,
    )

    _hook.on_firewall_triggered(item, "tool_result intercepted")
    logger.debug(
        "firewall: intercepted item_id=%s, strategy=%s, summary_len=%d",
        item.id,
        strategy,
        len(summary),
    )
    return processed, envelope


def apply_firewall_to_batch(
    items: list[ContextItem],
    artifact_store: ArtifactStore,
    hook: EventHook | None = None,
    view_registry: ViewRegistry | None = None,
    summarizer: Summarizer | None = None,
    extractor: Extractor | None = None,
    *,
    deterministic: bool = False,
    redact_secrets: bool = False,
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
        deterministic: Forwarded to each :func:`apply_firewall` call (issue
            #404).  When ``True`` the build fails closed rather than passing any
            item through an LLM-backed summariser.
        redact_secrets: Forwarded to each :func:`apply_firewall` call (issue
            #428).  When ``True`` summaries and facts are secret-scrubbed before
            they reach the prompt.

    Returns:
        A 2-tuple of ``(processed_items, envelopes)``.
    """
    processed = []
    envelopes = []
    for item in items:
        p, env = apply_firewall(
            item,
            artifact_store,
            hook,
            view_registry,
            summarizer,
            extractor,
            deterministic=deterministic,
            redact_secrets=redact_secrets,
        )
        processed.append(p)
        if env is not None:
            envelopes.append(env)
    logger.debug(
        "firewall_batch: processed=%d, intercepted=%d",
        len(processed),
        len(envelopes),
    )
    return processed, envelopes

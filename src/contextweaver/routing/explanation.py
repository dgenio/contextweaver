"""Routing-decision explanation rendering (issue #226).

Produces a human-readable rationale of *why* a :class:`RouteResult` ranked the
top-k candidates the way it did.  Extracted from :mod:`router` so the
``RouteResult.explanation()`` method stays a thin one-liner and ``router.py``
does not grow further beyond the soft 300-line cap.

The rendering is pure data — same input ``RouteResult`` → byte-identical
output.  No I/O, no randomness, no network calls.  Two surfaces:

- :func:`explain_route` returns a markdown string suitable for pasting into a
  GitHub issue body, a Slack thread, or a CLI dump.
- :func:`explain_route_dict` returns a versioned dict (``{"version": 1, ...}``)
  for programmatic consumers and downstream observability platforms.

Privacy guidance: this renderer surfaces item *ids*, *names*, *scores*, and the
original *query string*.  It does **not** surface ``args_schema`` content or
full item descriptions — those can carry sensitive payloads in some tool
catalogs (see ``docs/agent-context/invariants.md`` "Do not put schemas on
ChoiceCard").
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from contextweaver.routing.router import RouteResult

#: Schema version for :func:`explain_route_dict` payloads.  Bumped on
#: backwards-incompatible field changes (e.g. removing a key).
EXPLANATION_VERSION: int = 1


def explain_route_dict(result: RouteResult) -> dict[str, Any]:
    """Structured rationale dict for a :class:`RouteResult` (issue #226).

    The dict is versioned via the ``version`` key so downstream programmatic
    consumers can detect schema changes.  Output is deterministic given the
    same input.
    """
    paired: list[tuple[str, float]] = list(zip(result.candidate_ids, result.scores, strict=False))
    top_score = paired[0][1] if paired else 0.0
    runner_up_score = paired[1][1] if len(paired) > 1 else None
    confidence_gap = (top_score - runner_up_score) if runner_up_score is not None else None

    rank_one = paired[0] if paired else None
    rank_two = paired[1] if len(paired) > 1 else None

    return {
        "version": EXPLANATION_VERSION,
        "query": result.trace.query,
        "retriever_engine": result.trace.retriever_engine,
        "candidates": [
            {"rank": i + 1, "id": cid, "score": round(s, 4)} for i, (cid, s) in enumerate(paired)
        ],
        "top": {"id": rank_one[0], "score": round(rank_one[1], 4)} if rank_one else None,
        "runner_up": ({"id": rank_two[0], "score": round(rank_two[1], 4)} if rank_two else None),
        "confidence_gap": round(confidence_gap, 4) if confidence_gap is not None else None,
        "is_ambiguous": result.is_ambiguous,
        "clarifying_question": result.clarifying_question,
        "context_hints": list(result.context_hints),
        "context_boost_applied": result.context_boost_applied,
        "excluded_count": result.excluded_count,
        "gated_count": result.gated_count,
    }


def explain_route(
    result: RouteResult,
    format: Literal["md", "dict"] = "md",  # noqa: A002 — public API kwarg
) -> str | dict[str, Any]:
    """Render :class:`RouteResult` as a human-readable rationale (issue #226).

    Args:
        result: The :class:`RouteResult` to explain.
        format: ``"md"`` for a paste-friendly Markdown string;
            ``"dict"`` for the structured payload (see
            :func:`explain_route_dict`).

    Returns:
        A markdown string when ``format == "md"``, a dict otherwise.
    """
    payload = explain_route_dict(result)
    if format == "dict":
        return payload
    return _render_markdown(payload)


def _render_markdown(payload: dict[str, Any]) -> str:
    """Render the payload as deterministic, paste-friendly Markdown."""
    lines: list[str] = []
    query = payload.get("query") or "(no query)"
    lines.append(f"### Routing explanation for query `{query}`")
    lines.append("")
    lines.append(f"_Retriever engine: `{payload['retriever_engine']}`._")
    lines.append("")

    # Top-k table
    cands: list[dict[str, Any]] = payload["candidates"]
    if cands:
        lines.append("**Top candidates**")
        lines.append("")
        lines.append("| Rank | Tool id | Score |")
        lines.append("|---:|:---|---:|")
        for c in cands:
            lines.append(f"| {c['rank']} | `{c['id']}` | {c['score']:.4f} |")
        lines.append("")
    else:
        lines.append("**Top candidates**")
        lines.append("")
        lines.append("_No candidates returned._")
        lines.append("")

    # Confidence summary
    top = payload.get("top")
    runner_up = payload.get("runner_up")
    gap = payload.get("confidence_gap")
    if top is not None and runner_up is not None and gap is not None:
        lines.append(
            f"**Confidence gap**: `{top['id']}` ({top['score']:.4f}) "
            f"vs runner-up `{runner_up['id']}` ({runner_up['score']:.4f}) "
            f"= **{gap:+.4f}**."
        )
    elif top is not None:
        lines.append(f"**Top pick**: `{top['id']}` ({top['score']:.4f}) — no runner-up.")
    else:
        lines.append("**Confidence gap**: n/a (no candidates).")
    lines.append("")

    # Ambiguity
    if payload["is_ambiguous"]:
        lines.append("⚠️ **Ambiguous result** (gap below router's `confidence_gap` threshold).")
        cq = payload.get("clarifying_question")
        if cq:
            lines.append("")
            lines.append(f"> Suggested clarifying question: _{cq}_")
        lines.append("")
    else:
        lines.append("✅ Result is **not** ambiguous (gap above threshold).")
        lines.append("")

    # Filters applied
    excluded = payload["excluded_count"]
    gated = payload["gated_count"]
    if excluded or gated:
        lines.append("**Filters applied**")
        lines.append("")
        if excluded:
            lines.append(f"- {excluded} item(s) filtered by `exclude_ids` / `exclude_tags`.")
        if gated:
            lines.append(
                f"- {gated} item(s) filtered by `allowed_namespaces` /"
                " `allowed_tags` toolset gating."
            )
        lines.append("")

    # Context hints
    hints: list[str] = payload["context_hints"]
    if hints:
        lines.append("**Context hints applied**")
        lines.append("")
        for h in hints:
            lines.append(f"- {h}")
        if payload["context_boost_applied"]:
            lines.append("")
            lines.append("_Hints altered the scoring query._")
        else:
            lines.append("")
            lines.append("_Hints did not change the scoring query._")
        lines.append("")

    return "\n".join(lines).rstrip() + "\n"

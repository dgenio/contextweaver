"""Self-contained static HTML reports for routes, builds, and diagnostics (issue #442).

Three renderers turn contextweaver's diagnostic dataclasses into single-file
HTML pages an operator can save, attach to a ticket, or open offline:

- :func:`render_route_html` — a :class:`~contextweaver.routing.trace.RouteTrace`.
- :func:`render_build_html` — a :class:`~contextweaver.envelope.BuildStats`
  (optionally with a per-candidate
  :class:`~contextweaver.context.explanation.ContextBuildExplanation`).
- :func:`render_session_html` — a
  :class:`~contextweaver.diagnostics.DiagnosticEvent` timeline.

Every page is fully self-contained: inline CSS only, no scripts, no external
fonts, images, or network references of any kind.  All dynamic text is escaped
with :func:`html.escape` — tool names, descriptions, and queries are untrusted
upstream text (issue #480 adjacency) and must never be injected as markup.
Output is deterministic: the renderers add no timestamps of their own, so
identical inputs produce byte-identical pages.
"""

from __future__ import annotations

import html
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from contextweaver.context.explanation import ContextBuildExplanation
    from contextweaver.diagnostics import DiagnosticEvent
    from contextweaver.envelope import BuildStats
    from contextweaver.routing.trace import RouteTrace

__all__ = ["render_build_html", "render_route_html", "render_session_html"]

#: Minimal inline stylesheet shared by every page.  No external references.
_CSS = (
    "body{font-family:system-ui,sans-serif;margin:2rem;color:#1a1a2e;background:#fff}"
    "h1{font-size:1.4rem}h2{font-size:1.1rem;margin-top:1.6rem}"
    "table{border-collapse:collapse;margin-top:.5rem}"
    "th,td{border:1px solid #d0d0e0;padding:.3rem .6rem;text-align:left;font-size:.85rem}"
    "th{background:#f0f0f8}"
    ".tiles td:first-child{font-weight:600;background:#f0f0f8}"
    ".bar{background:#4a6fa5;height:.7rem;display:inline-block;vertical-align:middle}"
    ".barwrap{width:12rem;background:#eceef4;display:inline-block}"
    ".ok{color:#1d7a3d}.fail{color:#b3261e}"
)


def _esc(value: object) -> str:
    """Escape one dynamic value for safe HTML interpolation."""
    return html.escape(str(value), quote=True)


def _page(title: str, body: list[str]) -> str:
    """Wrap *body* rows in a complete standalone HTML document."""
    return (
        '<!DOCTYPE html>\n<html lang="en"><head><meta charset="utf-8">'
        f"<title>{_esc(title)}</title><style>{_CSS}</style></head>\n<body>\n"
        f"<h1>{_esc(title)}</h1>\n" + "\n".join(body) + "\n</body></html>\n"
    )


def _tiles(pairs: list[tuple[str, object]]) -> str:
    """Render summary key/value pairs as a two-column table."""
    rows = "".join(f"<tr><td>{_esc(k)}</td><td>{_esc(v)}</td></tr>" for k, v in pairs)
    return f'<table class="tiles">{rows}</table>'


def _table(headers: list[str], rows: list[list[str]]) -> str:
    """Render a table; *rows* cells must already be escaped/safe HTML."""
    head = "".join(f"<th>{_esc(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{cell}</td>" for cell in row) + "</tr>" for row in rows)
    return f"<table><tr>{head}</tr>{body}</table>"


def _bar(value: float, maximum: float) -> str:
    """Render *value* as an inline score bar scaled against *maximum*."""
    pct = 0.0 if maximum <= 0 else max(0.0, min(value / maximum, 1.0)) * 100
    return (
        f'<span class="barwrap"><span class="bar" style="width:{pct:.1f}%"></span></span> '
        f"{value:.4f}"
    )


def render_route_html(trace: RouteTrace, *, title: str = "contextweaver route trace") -> str:
    """Render a :class:`RouteTrace` as a self-contained HTML page.

    Args:
        trace: The routing audit record (``debug=True`` populates the
            per-step expansion table; the summary renders either way).
        title: Page title.

    Returns:
        A complete HTML document string.  Deterministic for identical inputs.
    """
    body = [
        "<h2>Summary</h2>",
        _tiles(
            [
                ("Query", trace.query),
                ("Retriever engine", trace.retriever_engine),
                ("Top score", f"{trace.top_score:.4f}"),
                (
                    "Runner-up score",
                    f"{trace.runner_up_score:.4f}" if trace.runner_up_score is not None else "-",
                ),
                ("Ambiguous", trace.is_ambiguous),
                ("Confidence gap threshold", f"{trace.confidence_gap:.4f}"),
                ("Excluded before scoring", trace.excluded_count),
                ("Gated before scoring", trace.gated_count),
            ]
        ),
    ]
    if trace.clarifying_question:
        body.append(f"<p>Clarifying question: {_esc(trace.clarifying_question)}</p>")
    body.append("<h2>Beam steps</h2>")
    if not trace.steps:
        body.append("<p>No step expansions recorded (route with debug=True to capture them).</p>")
    else:
        max_score = max(
            (score for step in trace.steps for _, score in step.scored_children), default=0.0
        )
        rows = [
            [
                _esc(step.depth),
                _esc(step.node),
                _esc(child_id),
                _bar(score, max_score),
                "yes" if child_id in step.kept else "no",
            ]
            for step in trace.steps
            for child_id, score in step.scored_children
        ]
        body.append(_table(["Depth", "Node", "Candidate", "Score", "Kept"], rows))
    return _page(title, body)


def render_build_html(
    stats: BuildStats,
    *,
    explanation: ContextBuildExplanation | None = None,
    title: str = "contextweaver context build",
) -> str:
    """Render a :class:`BuildStats` (and optional explanation) as HTML.

    Args:
        stats: The build's diagnostic statistics.
        explanation: Optional ``build(..., explain=True)`` output; adds a
            per-candidate table with score bars.
        title: Page title.

    Returns:
        A complete HTML document string.  Deterministic for identical inputs.
    """
    firewall = stats.firewall_summary()
    body = [
        "<h2>Summary</h2>",
        _tiles(
            [
                ("Prompt tokens", stats.prompt_tokens),
                ("Candidates", stats.total_candidates),
                ("Included", stats.included_count),
                ("Dropped", stats.dropped_count),
                ("Deduplicated", stats.dedup_removed),
                ("Dependency closures", stats.dependency_closures),
                ("Token estimator", stats.token_estimator or "-"),
                ("Firewall triggered", firewall.triggered),
                ("Firewall strategy", firewall.strategy),
                ("Firewall tokens saved", firewall.tokens_saved),
            ]
        ),
        "<h2>Tokens per section</h2>",
    ]
    sections = sorted(stats.tokens_per_section.items())
    if sections:
        max_tokens = max(tokens for _, tokens in sections)
        rows = [
            [_esc(name), _esc(tokens), _bar(float(tokens), float(max_tokens))]
            for name, tokens in sections
        ]
        body.append(_table(["Section", "Tokens", "Share"], rows))
    else:
        body.append("<p>No sections rendered.</p>")
    body.append("<h2>Dropped items</h2>")
    if stats.dropped_items:
        rows = [[_esc(item.item_id), _esc(item.reason)] for item in stats.dropped_items]
        body.append(_table(["Item", "Reason"], rows))
    else:
        body.append("<p>None.</p>")
    if explanation is not None:
        body.append("<h2>Candidates</h2>")
        max_score = max(
            (c.score for c in explanation.candidates if c.score is not None), default=0.0
        )
        rows = [
            [
                _esc(c.item_id),
                _esc(c.kind),
                _esc(c.sensitivity),
                _bar(c.score, max_score) if c.score is not None else "-",
                '<span class="ok">included</span>'
                if c.included
                else f'<span class="fail">{_esc(c.drop_reason or "dropped")}</span>',
            ]
            for c in explanation.candidates
        ]
        body.append(_table(["Item", "Kind", "Sensitivity", "Score", "Outcome"], rows))
    return _page(title, body)


def render_session_html(
    events: list[DiagnosticEvent], *, title: str = "contextweaver session diagnostics"
) -> str:
    """Render a gateway diagnostic-event timeline as HTML.

    Args:
        events: Events in stream order (e.g. from
            :func:`~contextweaver.diagnostics.load_diagnostic_events`).
        title: Page title.

    Returns:
        A complete HTML document string.  Deterministic for identical inputs —
        timestamps shown are the events' own; the renderer adds none.
    """
    families: dict[str, int] = {}
    failures = 0
    for event in events:
        family = event.event.split(".", 1)[0]
        families[family] = families.get(family, 0) + 1
        failures += not event.success
    body = [
        "<h2>Summary</h2>",
        _tiles(
            [
                ("Events", len(events)),
                ("Failures", failures),
                ("Sessions", len({e.session_id for e in events if e.session_id})),
            ]
        ),
        "<h2>Events by family</h2>",
        _table(
            ["Family", "Count"],
            [[_esc(name), _esc(count)] for name, count in sorted(families.items())],
        ),
        "<h2>Timeline</h2>",
    ]
    if events:
        rows = [
            [
                _esc(event.timestamp),
                _esc(event.event),
                '<span class="ok">ok</span>' if event.success else '<span class="fail">fail</span>',
                _esc(f"{event.duration_ms:.3f}") if event.duration_ms is not None else "-",
                _esc(event.tool_id or "-"),
                _esc(event.namespace or "-"),
            ]
            for event in events
        ]
        body.append(_table(["Timestamp", "Event", "Status", "ms", "Tool", "Namespace"], rows))
    else:
        body.append("<p>No events.</p>")
    return _page(title, body)

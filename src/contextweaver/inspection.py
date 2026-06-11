"""Offline diagnostic report construction for context builds and routes."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Iterable

    from contextweaver.context.explanation import ContextBuildExplanation
    from contextweaver.envelope import ContextPack

INSPECTION_REPORT_VERSION: int = 1


def build_inspection_report(
    pack: ContextPack,
    *,
    explanation: ContextBuildExplanation | None = None,
    artifacts: Iterable[dict[str, Any]] = (),
    routing: dict[str, Any] | None = None,
    budget: int | None = None,
) -> dict[str, Any]:
    """Build a payload-safe context/routing/artifact diagnostic report.

    Raw prompt text, event text, queries, argument values, and artifact bytes
    are intentionally excluded. Candidate and artifact identifiers remain so
    operators can drill down through their own stores.
    """
    candidates: list[dict[str, Any]] = []
    if explanation is not None:
        candidates = [
            {
                "item_id": item.item_id,
                "kind": item.kind,
                "sensitivity": item.sensitivity,
                "score": round(item.score, 4) if item.score is not None else None,
                "included": item.included,
                "drop_reason": item.drop_reason,
                "dependency_closure": item.dependency_closure,
            }
            for item in explanation.candidates
        ]
    return {
        "version": INSPECTION_REPORT_VERSION,
        "phase": pack.phase.value,
        "build": pack.stats.report_dict(phase=pack.phase.value, budget=budget),
        "candidates": candidates,
        "artifacts": sorted(
            (dict(item) for item in artifacts),
            key=lambda item: str(item.get("handle", "")),
        ),
        "routing": routing,
    }


def render_inspection_report(report: dict[str, Any]) -> str:
    """Render an inspection payload as deterministic Markdown."""
    build = report["build"]
    candidates = build["candidates"]
    lines = [
        "# Context Inspection",
        "",
        f"- Phase: {report.get('phase', '')}",
        f"- Prompt tokens: {build.get('prompt_tokens', 0)}",
        f"- Candidates: {candidates.get('total', 0)} total, "
        f"{candidates.get('included', 0)} included, "
        f"{candidates.get('dropped', 0)} dropped",
        "",
        "## Drops",
    ]
    dropped_items = build.get("dropped_items", [])
    if dropped_items:
        for item in dropped_items:
            lines.append(f"- `{item['item_id']}`: {item['reason']}")
    else:
        lines.append("- None")

    lines.extend(["", "## Artifacts"])
    artifacts = report.get("artifacts", [])
    if artifacts:
        for item in artifacts:
            lines.append(
                f"- `{item.get('handle', '')}`: {item.get('media_type', '')}, "
                f"{item.get('size_bytes', 0)} bytes"
            )
    else:
        lines.append("- None")

    routing = report.get("routing")
    lines.extend(["", "## Routing"])
    if routing:
        ids = routing.get("candidate_ids", [])
        lines.append(f"- Candidates: {len(ids)}")
        for item_id, score in zip(ids, routing.get("scores", []), strict=False):
            lines.append(f"- `{item_id}`: {score}")
    else:
        lines.append("- Not requested")
    return "\n".join(lines) + "\n"

"""View registry and progressive disclosure helpers for contextweaver.

Provides automatic :class:`~contextweaver.types.ViewSpec` generation,
a content-type view registry, and ``drilldown_tool_spec()``.
"""

from __future__ import annotations

import csv
import json
from collections.abc import Callable

from contextweaver.types import ArtifactRef, SelectableItem, ViewSpec

ViewGenerator = Callable[[ArtifactRef, bytes], list[ViewSpec]]


def _json_views(ref: ArtifactRef, data: bytes) -> list[ViewSpec]:
    """Generate views for JSON content."""
    text = data.decode("utf-8", errors="replace")
    try:
        obj = json.loads(text)
    except (json.JSONDecodeError, ValueError):
        return []

    views: list[ViewSpec] = []

    if isinstance(obj, dict) and obj:
        keys = sorted(obj.keys())
        views.append(
            ViewSpec(
                view_id=f"{ref.handle}:json_keys",
                label=f"JSON keys: {', '.join(keys[:5])}" + ("…" if len(keys) > 5 else ""),
                selector={"type": "json_keys", "keys": keys},
                artifact_ref=ref,
            )
        )
        # Individual key views for up to 10 top-level keys
        for key in keys[:10]:
            views.append(
                ViewSpec(
                    view_id=f"{ref.handle}:key:{key}",
                    label=f"JSON key '{key}'",
                    selector={"type": "json_keys", "keys": [key]},
                    artifact_ref=ref,
                )
            )

    if isinstance(obj, list) and obj:
        views.append(
            ViewSpec(
                view_id=f"{ref.handle}:array_head",
                label=f"Array head ({min(len(obj), 5)} of {len(obj)} items)",
                selector={"type": "head", "chars": 500},
                artifact_ref=ref,
            )
        )

    # Always offer a head view for non-trivial JSON
    if len(text) > 200:
        views.append(
            ViewSpec(
                view_id=f"{ref.handle}:head",
                label="Head (500 chars)",
                selector={"type": "head", "chars": 500},
                artifact_ref=ref,
            )
        )

    return views


def _csv_views(ref: ArtifactRef, data: bytes) -> list[ViewSpec]:
    """Generate views for CSV/TSV content."""
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    total = len(lines)

    views: list[ViewSpec] = []

    if total > 0:
        views.append(
            ViewSpec(
                view_id=f"{ref.handle}:head_rows",
                label=f"Head rows (first {min(total, 10)} of {total})",
                selector={"type": "rows", "start": 0, "end": min(total, 10)},
                artifact_ref=ref,
            )
        )

    if total > 10:
        views.append(
            ViewSpec(
                view_id=f"{ref.handle}:tail_rows",
                label=f"Tail rows (last {min(total, 10)} of {total})",
                selector={"type": "rows", "start": max(0, total - 10), "end": total},
                artifact_ref=ref,
            )
        )

    return views


def _text_views(ref: ArtifactRef, data: bytes) -> list[ViewSpec]:
    """Generate views for plain text content."""
    text = data.decode("utf-8", errors="replace")
    lines = text.splitlines()
    total = len(lines)

    views: list[ViewSpec] = []

    if total > 0:
        views.append(
            ViewSpec(
                view_id=f"{ref.handle}:head",
                label=f"Head ({min(total, 20)} lines)",
                selector={"type": "lines", "start": 0, "end": min(total, 20)},
                artifact_ref=ref,
            )
        )

    if total > 20:
        views.append(
            ViewSpec(
                view_id=f"{ref.handle}:tail",
                label=f"Tail ({min(total, 20)} lines)",
                selector={"type": "lines", "start": max(0, total - 20), "end": total},
                artifact_ref=ref,
            )
        )

    return views


def _binary_views(ref: ArtifactRef, data: bytes) -> list[ViewSpec]:
    """Generate a header-inspection view for binary/image content."""
    return [
        ViewSpec(
            view_id=f"{ref.handle}:meta",
            label=f"Header (128 bytes, {ref.media_type})",
            selector={"type": "head", "chars": 128},
            artifact_ref=ref,
        )
    ]


# ---------------------------------------------------------------------------
# Content-type detection
# ---------------------------------------------------------------------------


def _detect_content_type(data: bytes, media_type: str) -> str:
    """Detect effective content type using media_type hint and heuristics."""
    if media_type.startswith("image/"):
        return media_type
    if media_type == "application/octet-stream":
        try:
            text = data.decode("utf-8")
        except (UnicodeDecodeError, ValueError):
            return media_type
        text = text.strip()
        if text and text[0] in ("{", "["):
            try:
                json.loads(text)
                return "application/json"
            except (json.JSONDecodeError, ValueError):
                pass
        if _looks_like_csv(text):
            return "text/csv"
        return "text/plain"

    return media_type


def _looks_like_csv(text: str) -> bool:
    """Heuristic: does the text look like CSV/TSV?"""
    lines = text.splitlines()
    if len(lines) < 2:
        return False
    try:
        sniffer = csv.Sniffer()
        sample = "\n".join(lines[:5])
        sniffer.sniff(sample)
        return True
    except csv.Error:
        return False


# ---------------------------------------------------------------------------
# ViewRegistry
# ---------------------------------------------------------------------------


class ViewRegistry:
    """Maps content-type patterns to :class:`ViewSpec` generators.

    Built-in generators handle ``application/json``, ``text/csv``,
    ``text/plain``, and binary/image content.  Users extend via :meth:`register`.
    """

    def __init__(self) -> None:
        self._generators: dict[str, ViewGenerator] = {
            "application/json": _json_views,
            "text/csv": _csv_views,
            "text/plain": _text_views,
        }

    def register(self, content_type: str, generator: ViewGenerator) -> None:
        """Register a view generator for a content type."""
        self._generators[content_type] = generator

    def generate_views(self, ref: ArtifactRef, data: bytes) -> list[ViewSpec]:
        """Auto-generate :class:`ViewSpec` entries for an artifact.

        Detects effective content type and delegates to the matching
        generator.  Falls back to a binary/metadata view for unknown types.
        """
        effective = _detect_content_type(data, ref.media_type)

        # Exact match
        if effective in self._generators:
            return self._generators[effective](ref, data)

        # Prefix match for text/* types only — prefer text/plain as
        # the general fallback within the text family.
        if effective.startswith("text/") and "text/plain" in self._generators:
            return self._generators["text/plain"](ref, data)

        # Fallback: binary metadata view
        return _binary_views(ref, data)


def generate_views(
    ref: ArtifactRef,
    data: bytes,
    registry: ViewRegistry | None = None,
) -> list[ViewSpec]:
    """Auto-generate views for an artifact using the given or default registry."""
    reg = registry or ViewRegistry()
    return reg.generate_views(ref, data)


# ---------------------------------------------------------------------------
# Drilldown tool spec
# ---------------------------------------------------------------------------


def drilldown_tool_spec() -> SelectableItem:
    """Return a :class:`SelectableItem` describing the drilldown action.

    Add to the agent's tool catalog during ``interpret`` phase so the
    agent can call drilldown to fetch artifact slices.
    """
    return SelectableItem(
        id="contextweaver:drilldown",
        kind="internal",
        name="drilldown",
        description=(
            "Fetch a specific slice of a stored artifact. "
            "Provide an artifact handle and a selector to retrieve "
            "only the data you need."
        ),
        tags=["progressive-disclosure", "drilldown", "artifact"],
        args_schema={
            "type": "object",
            "properties": {
                "handle": {"type": "string", "description": "Artifact handle."},
                "selector": {
                    "type": "object",
                    "description": 'Selector: "head", "lines", "json_keys", or "rows".',
                    "properties": {
                        "type": {
                            "type": "string",
                            "enum": ["head", "lines", "json_keys", "rows"],
                        },
                    },
                    "required": ["type"],
                },
            },
            "required": ["handle", "selector"],
        },
        metadata={"builtin": True, "phase": "interpret"},
    )

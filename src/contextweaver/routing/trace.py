"""Structured routing trace for audit and reproducibility.

A :class:`RouteTrace` is the authoritative, machine-readable record of
what the :class:`~contextweaver.routing.router.Router` did during a
single :meth:`Router.route` call.  Unlike the legacy ``debug_trace`` —
an opt-in ``list[dict]`` populated only when ``debug=True`` — the trace
is always available via :attr:`RouteResult.trace` and exposes typed
fields that downstream tools can rely on.

The trace is engine-version aware: each entry records the engine slot
in use (``"tfidf"``, ``"bm25"``, …) so that traces from different
backends can be diffed without ambiguity.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from contextweaver._deprecation import deprecated

#: Trace schema version.  Bumped on backwards-incompatible field changes.
TRACE_VERSION: int = 1


@dataclass
class TraceStep:
    """One beam-search expansion in a :class:`RouteTrace`.

    Attributes:
        depth: Tree depth at which this expansion occurred (root = 0).
        node: ID of the node being expanded.
        scored_children: Ordered list of ``(child_id, score)`` pairs
            considered at this expansion, descending by score.
        kept: IDs of the children that were kept on the active beam.
    """

    depth: int
    node: str
    scored_children: list[tuple[str, float]] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "depth": self.depth,
            "node": self.node,
            "scored_children": [
                {"id": cid, "score": round(cs, 4)} for cid, cs in self.scored_children
            ],
            "kept": list(self.kept),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TraceStep:
        """Deserialise from a JSON-compatible dict."""
        scored_raw = data.get("scored_children", [])
        scored: list[tuple[str, float]] = []
        for entry in scored_raw:
            if isinstance(entry, dict):
                scored.append((str(entry["id"]), float(entry["score"])))
            else:
                # Tolerate a (id, score) tuple form.
                cid, cs = entry
                scored.append((str(cid), float(cs)))
        return cls(
            depth=int(data.get("depth", 0)),
            node=str(data.get("node", "")),
            scored_children=scored,
            kept=list(data.get("kept", [])),
        )


@dataclass
class RouteTrace:
    """Structured audit record of a single routing call.

    Attributes:
        trace_version: Schema version (currently :data:`TRACE_VERSION`).
        query: The user query string passed to :meth:`Router.route`.
        confidence_gap: The router's configured confidence-gap threshold.
        top_score: Score of the rank-1 candidate (``0.0`` if no results).
        runner_up_score: Score of the rank-2 candidate, or ``None``.
        is_ambiguous: ``True`` when the rank-1/rank-2 gap is below
            *confidence_gap* (issue #14).
        excluded_count: Number of items removed by ``exclude_ids`` /
            ``exclude_tags`` filters before scoring (issue #112).
        gated_count: Number of items removed by ``allowed_namespaces`` /
            ``allowed_tags`` toolset gating before scoring (issue #22).
        retriever_engine: Name of the retriever engine in use.
        steps: Per-depth beam-search expansions.  Populated only when
            the router is invoked with ``debug=True``; ``[]`` otherwise.
        clarifying_question: Optional surface text suggesting a
            disambiguation question (issue #14).  ``None`` when not
            ambiguous or when no template applies.
        extra: Free-form additional metadata for downstream tooling.
    """

    trace_version: int = TRACE_VERSION
    query: str = ""
    confidence_gap: float = 0.0
    top_score: float = 0.0
    runner_up_score: float | None = None
    is_ambiguous: bool = False
    excluded_count: int = 0
    gated_count: int = 0
    retriever_engine: str = "tfidf"
    steps: list[TraceStep] = field(default_factory=list)
    clarifying_question: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "trace_version": self.trace_version,
            "query": self.query,
            "confidence_gap": self.confidence_gap,
            "top_score": self.top_score,
            "runner_up_score": self.runner_up_score,
            "is_ambiguous": self.is_ambiguous,
            "excluded_count": self.excluded_count,
            "gated_count": self.gated_count,
            "retriever_engine": self.retriever_engine,
            "steps": [s.to_dict() for s in self.steps],
            "clarifying_question": self.clarifying_question,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RouteTrace:
        """Deserialise from a JSON-compatible dict."""
        runner_up = data.get("runner_up_score")
        return cls(
            trace_version=int(data.get("trace_version", TRACE_VERSION)),
            query=str(data.get("query", "")),
            confidence_gap=float(data.get("confidence_gap", 0.0)),
            top_score=float(data.get("top_score", 0.0)),
            runner_up_score=float(runner_up) if runner_up is not None else None,
            is_ambiguous=bool(data.get("is_ambiguous", False)),
            excluded_count=int(data.get("excluded_count", 0)),
            gated_count=int(data.get("gated_count", 0)),
            retriever_engine=str(data.get("retriever_engine", "tfidf")),
            steps=[TraceStep.from_dict(s) for s in data.get("steps", [])],
            clarifying_question=data.get("clarifying_question"),
            extra=dict(data.get("extra", {})),
        )

    @deprecated(
        "RouteTrace.to_legacy_dicts",
        since="0.16.0",
        removal="1.0.0",
        instead="the structured RouteTrace fields (steps / to_dict)",
    )
    def to_legacy_dicts(self) -> list[dict[str, Any]]:
        """Return the legacy ``debug_trace`` shape for backwards compatibility.

        The legacy format groups per-depth expansions under a list of
        ``{"depth": int, "expansions": [...]}`` records.  This method
        reconstructs that shape from :attr:`steps`.

        .. deprecated:: 0.16.0
            Use the structured :class:`RouteTrace` fields (:attr:`steps` /
            :meth:`to_dict`); scheduled for removal in 1.0.0 (issue #642).
        """
        return self._to_legacy_dicts()

    def _to_legacy_dicts(self) -> list[dict[str, Any]]:
        """Construct the legacy ``debug_trace`` shape (no deprecation warning).

        Internal helper shared by the deprecated public :meth:`to_legacy_dicts`
        and :attr:`RouteResult.debug_trace` so that in-library callers do not
        trip the deprecation warning on the canonical code path.
        """
        by_depth: dict[int, list[dict[str, Any]]] = {}
        for step in self.steps:
            by_depth.setdefault(step.depth, []).append(
                {
                    "node": step.node,
                    "scored_children": [
                        {"id": cid, "score": round(cs, 4)} for cid, cs in step.scored_children
                    ],
                }
            )
        return [
            {"depth": d, "expansions": expansions} for d, expansions in sorted(by_depth.items())
        ]

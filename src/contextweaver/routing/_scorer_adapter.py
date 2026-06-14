"""Internal ``Retriever`` adapter for the legacy ``Router(scorer=...)`` shim.

Extracted from :mod:`contextweaver.routing.router` so that the grandfathered
``router.py`` stays within its frozen module-size ceiling
(``scripts/module_size_baseline.json``) while still carrying the deprecated
``scorer=`` constructor path (issue #642).  Pure synchronous computation with no
imports from :mod:`contextweaver.context`, so the routing engine's sync-only
boundary is preserved.
"""

from __future__ import annotations

from typing import Any

from contextweaver._utils import BM25Scorer, TfIdfScorer

# Union of all scorer types Router accepts. ``FuzzyScorer`` is ``None`` when
# the ``contextweaver[retrieval]`` extra is not installed; we widen with
# ``Any`` rather than naming the runtime ``None`` sentinel here.
_ScorerLike = TfIdfScorer | BM25Scorer | Any


class _ScorerRetriever:
    """Internal :class:`Retriever` adapter for legacy ``scorer=`` callers.

    Wraps any pre-existing scorer that exposes the ``fit(corpus)`` /
    ``score(query, index)`` shape (e.g. :class:`TfIdfScorer`,
    :class:`BM25Scorer`, :class:`FuzzyScorer`) so the rest of
    :class:`~contextweaver.routing.router.Router` can talk to a single
    :class:`Retriever` surface regardless of how the engine was supplied.
    """

    def __init__(self, scorer: _ScorerLike) -> None:
        self._scorer = scorer
        self._corpus_size = 0

    def fit(self, corpus: list[str]) -> None:
        self._scorer.fit(corpus)
        self._corpus_size = len(corpus)

    def search(self, query: str, top_k: int) -> list[tuple[int, float]]:
        scored = [(i, self._scorer.score(query, i)) for i in range(self._corpus_size)]
        scored.sort(key=lambda x: (-x[1], x[0]))
        return scored[: max(0, top_k)]

    def score_one(self, query: str, index: int) -> float:
        if not 0 <= index < self._corpus_size:
            return 0.0
        return self._scorer.score(query, index)

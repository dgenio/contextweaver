"""Engine registry for the contextweaver Routing Engine.

The :class:`EngineRegistry` is a small, in-process registry that maps
named engine slots — ``"retriever"``, ``"reranker"``, ``"clustering"`` —
to factory callables.  Routing components (:class:`TreeBuilder`,
:class:`Router`) consult the registry to obtain engine instances at
runtime, allowing alternative algorithm backends to be wired in without
modifying core code.

The default registry instance, :data:`default_registry`, ships with the
in-tree default implementations:

* ``"tfidf"`` retriever — wraps :class:`~contextweaver.\\_utils.TfIdfScorer`
  in the :class:`~contextweaver.protocols.Retriever` protocol
* ``"identity"`` reranker — :class:`NoOpReranker`, which leaves order
  untouched
* ``"jaccard"`` clustering engine — :class:`JaccardClusteringEngine`,
  which mirrors the existing farthest-first behaviour in
  :class:`TreeBuilder`

The registry is intentionally stdlib-only and synchronous; it does not
load entry points, perform plugin discovery, or scan for classes.
Callers register engines explicitly via :meth:`EngineRegistry.register`.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from contextweaver._utils import TfIdfScorer, jaccard, tokenize
from contextweaver.exceptions import ConfigError
from contextweaver.types import SelectableItem

#: Recognised engine slot names.  Adding a new slot requires updating
#: this set so that mistyped slot names are rejected at registration
#: time rather than silently ignored.
ENGINE_SLOTS: frozenset[str] = frozenset({"retriever", "reranker", "clustering"})


# ---------------------------------------------------------------------------
# Default implementations
# ---------------------------------------------------------------------------


class TfIdfRetriever:
    """:class:`Retriever` adapter around the in-tree :class:`TfIdfScorer`.

    This is the default ``"retriever"`` engine in
    :data:`default_registry`.  It is fully deterministic and adds no new
    dependencies.
    """

    def __init__(self) -> None:
        self._scorer: TfIdfScorer | None = None
        self._corpus_size = 0

    def fit(self, corpus: list[str]) -> None:
        """Fit the underlying TF-IDF index on *corpus*."""
        self._scorer = TfIdfScorer()
        self._scorer.fit(corpus)
        self._corpus_size = len(corpus)

    def search(self, query: str, top_k: int) -> list[tuple[int, float]]:
        """Return the top-*top_k* ``(index, score)`` pairs for *query*."""
        if self._scorer is None:
            return []
        scored = [(i, self._scorer.score(query, i)) for i in range(self._corpus_size)]
        # Descending score, ascending index for ties (determinism).
        scored.sort(key=lambda x: (-x[1], x[0]))
        return scored[: max(0, top_k)]


class NoOpReranker:
    """Default :class:`Reranker` that returns its input unchanged."""

    def rerank(
        self,
        query: str,
        candidates: list[tuple[str, float]],
    ) -> list[tuple[str, float]]:
        """Return *candidates* unchanged.  *query* is unused."""
        _ = query
        return list(candidates)


class JaccardClusteringEngine:
    """Default :class:`ClusteringEngine` using farthest-first Jaccard seeding.

    Mirrors the in-line clustering logic that lives in
    :class:`~contextweaver.routing.tree.TreeBuilder` so that the same
    algorithm is reachable via the registry.  Custom engines can be
    swapped in by registering an alternative under the
    ``"clustering"`` slot.
    """

    def cluster(
        self,
        items: list[SelectableItem],
        *,
        k: int,
    ) -> dict[str, list[SelectableItem]]:
        """Cluster *items* into at most *k* groups via farthest-first seeding."""
        if not items:
            return {}
        if k <= 1 or len(items) <= 1:
            return {"cluster_000": list(items)}

        sorted_items = sorted(items, key=lambda it: it.id)
        token_sets = [
            tokenize(f"{it.name} {it.description} {' '.join(it.tags)}") for it in sorted_items
        ]

        seeds = [0]
        for _ in range(min(k, len(sorted_items)) - 1):
            best_idx = -1
            best_min_dist = -1.0
            for i in range(len(sorted_items)):
                if i in seeds:
                    continue
                min_dist = min(1.0 - jaccard(token_sets[i], token_sets[s]) for s in seeds)
                if min_dist > best_min_dist:
                    best_min_dist = min_dist
                    best_idx = i
            if best_idx >= 0:
                seeds.append(best_idx)

        assignments: dict[int, list[int]] = {s: [] for s in seeds}
        for i in range(len(sorted_items)):
            best_seed = seeds[0]
            best_sim = -1.0
            for s in seeds:
                sim = jaccard(token_sets[i], token_sets[s])
                if sim > best_sim or (sim == best_sim and s < best_seed):
                    best_sim = sim
                    best_seed = s
            assignments[best_seed].append(i)

        groups: dict[str, list[SelectableItem]] = {}
        cluster_idx = 0
        for seed_idx in sorted(assignments):
            members = assignments[seed_idx]
            if not members:
                continue
            groups[f"cluster_{cluster_idx:03d}"] = [sorted_items[i] for i in members]
            cluster_idx += 1
        return groups


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


#: Type alias for engine factory callables.  A factory is any zero-arg
#: callable that produces a fresh engine instance on each call.
EngineFactory = Callable[[], Any]


class EngineRegistry:
    """In-process registry for pluggable routing engines.

    Engines are registered under a *slot* (``"retriever"``, ``"reranker"``,
    or ``"clustering"``) by *name*.  Resolving a slot returns a fresh
    engine instance produced by the registered factory.

    Determinism: registry lookups themselves are deterministic.  The
    determinism of resolved engines is the engine's responsibility — the
    bundled defaults (:class:`TfIdfRetriever`, :class:`NoOpReranker`,
    :class:`JaccardClusteringEngine`) are all deterministic.
    """

    def __init__(self) -> None:
        self._factories: dict[str, dict[str, EngineFactory]] = {slot: {} for slot in ENGINE_SLOTS}
        self._defaults: dict[str, str] = {}

    def register(
        self,
        slot: str,
        name: str,
        factory: EngineFactory,
        *,
        default: bool = False,
    ) -> None:
        """Register *factory* under (*slot*, *name*).

        Args:
            slot: One of :data:`ENGINE_SLOTS`.
            name: Engine name (e.g. ``"tfidf"``, ``"bm25"``).  Re-registering
                the same name overwrites the previous factory.
            factory: Zero-arg callable returning a fresh engine instance.
            default: When ``True``, mark this engine as the slot's default.

        Raises:
            ConfigError: If *slot* is not a recognised slot name.
        """
        if slot not in ENGINE_SLOTS:
            valid = ", ".join(sorted(ENGINE_SLOTS))
            raise ConfigError(f"Unknown engine slot {slot!r}. Valid slots: {valid}.")
        self._factories[slot][name] = factory
        if default or slot not in self._defaults:
            self._defaults[slot] = name

    def resolve(self, slot: str, name: str | None = None) -> Any:  # noqa: ANN401
        """Construct and return a fresh engine for (*slot*, *name*).

        The return type is intentionally ``Any``: each slot has a
        distinct engine protocol (:class:`Retriever`, :class:`Reranker`,
        :class:`ClusteringEngine`) and the registry is the universal
        lookup point.  Callers downcast to the slot-specific protocol.

        Args:
            slot: One of :data:`ENGINE_SLOTS`.
            name: Engine name.  When ``None``, the slot's default is used.

        Returns:
            A new engine instance produced by the registered factory.

        Raises:
            ConfigError: If *slot* is unknown, no default is registered,
                or *name* is not registered under *slot*.
        """
        if slot not in ENGINE_SLOTS:
            valid = ", ".join(sorted(ENGINE_SLOTS))
            raise ConfigError(f"Unknown engine slot {slot!r}. Valid slots: {valid}.")
        resolved_name = name if name is not None else self._defaults.get(slot)
        if resolved_name is None:
            raise ConfigError(f"No default engine registered for slot {slot!r}.")
        factory = self._factories[slot].get(resolved_name)
        if factory is None:
            valid = ", ".join(sorted(self._factories[slot]))
            raise ConfigError(
                f"Engine {resolved_name!r} not registered under slot {slot!r}. "
                f"Available: {valid or '(none)'}."
            )
        return factory()

    def list_engines(self, slot: str) -> list[str]:
        """Return the registered engine names for *slot* in sorted order."""
        if slot not in ENGINE_SLOTS:
            valid = ", ".join(sorted(ENGINE_SLOTS))
            raise ConfigError(f"Unknown engine slot {slot!r}. Valid slots: {valid}.")
        return sorted(self._factories[slot])

    def default_for(self, slot: str) -> str | None:
        """Return the default engine name for *slot*, or ``None``."""
        if slot not in ENGINE_SLOTS:
            return None
        return self._defaults.get(slot)


def _build_default_registry() -> EngineRegistry:
    """Construct the package's default :class:`EngineRegistry`."""
    registry = EngineRegistry()
    registry.register("retriever", "tfidf", TfIdfRetriever, default=True)
    registry.register("reranker", "identity", NoOpReranker, default=True)
    registry.register("clustering", "jaccard", JaccardClusteringEngine, default=True)
    return registry


#: Module-level default registry pre-populated with the in-tree engines.
#: Callers may :meth:`~EngineRegistry.register` additional engines on
#: this instance or construct their own :class:`EngineRegistry`.
default_registry: EngineRegistry = _build_default_registry()


__all__ = [
    "ENGINE_SLOTS",
    "EngineFactory",
    "EngineRegistry",
    "JaccardClusteringEngine",
    "NoOpReranker",
    "TfIdfRetriever",
    "default_registry",
]

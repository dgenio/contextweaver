"""Graph manifest — formal build metadata for :class:`ChoiceGraph`.

A :class:`GraphManifest` records the inputs, engine versions, and timing
that produced a particular routing graph.  It is attached to every graph
built by :class:`~contextweaver.routing.tree.TreeBuilder` (via
``ChoiceGraph.build_meta``) and survives :meth:`ChoiceGraph.to_dict` /
:meth:`ChoiceGraph.from_dict` round-trips.

The manifest enables:

* Cache invalidation — :class:`TreeBuilder` compares the catalog hash on
  the manifest with the hash of new input items to decide whether to
  rebuild or reuse.
* Reproducibility — the seed and engine versions are exact enough to
  reproduce a graph from the original catalog.
* Drift detection — comparing two manifests reveals which inputs
  changed between builds.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from contextweaver.types import SelectableItem

#: Manifest schema version.  Bumped when the manifest dict shape changes
#: in a backwards-incompatible way.  Version 1 = initial schema.
MANIFEST_VERSION: int = 1


def _sha256_hex(data: str) -> str:
    """Return the SHA-256 hex digest of *data*."""
    return hashlib.sha256(data.encode("utf-8")).hexdigest()


def compute_catalog_hash(items: list[SelectableItem]) -> str:
    """Compute a deterministic SHA-256 hash of *items* for cache invalidation.

    The hash is invariant under list reordering (items are sorted by id)
    and reflects every field that influences the routing graph: id, name,
    description, tags, and namespace.  Other fields (examples, metadata)
    are intentionally excluded so that catalog metadata edits which do
    not affect routing do not invalidate the cached graph.

    Args:
        items: Catalog items.

    Returns:
        A hex-encoded SHA-256 digest.
    """
    parts: list[str] = []
    for item in sorted(items, key=lambda it: it.id):
        tags_str = ",".join(sorted(item.tags))
        parts.append(f"{item.id}|{item.name}|{item.description}|{item.namespace}|{tags_str}")
    return _sha256_hex("\n".join(parts))


@dataclass
class GraphManifest:
    """Formal build metadata for a :class:`ChoiceGraph`.

    Attributes:
        manifest_version: Schema version (currently :data:`MANIFEST_VERSION`).
        build_hash: Stable SHA-256 hash of the input catalog used by the
            cache key.  Empty string if the manifest was constructed
            without a catalog reference.
        seed: Optional integer seed for forward-compatible seeded modes.
            ``None`` in :class:`~contextweaver.config.Mode.strict`.
        engine_versions: Mapping of engine slot name (``"retriever"``,
            ``"reranker"``, ``"clustering"``, ``"labeler"``) to a
            human-readable version string.  Engine slots not in use may
            be omitted.
        timestamp: Unix timestamp (seconds since epoch) at build time.
        item_count: Number of items in the source catalog.
        strategy: Tree-building strategy that won (e.g. ``"namespace"``,
            ``"clustering"``, ``"alphabetical"``, ``"auto"``).
        max_depth: Maximum depth of the resulting graph.
        extra: Free-form additional metadata for downstream tooling.
    """

    manifest_version: int = MANIFEST_VERSION
    build_hash: str = ""
    seed: int | None = None
    engine_versions: dict[str, str] = field(default_factory=dict)
    timestamp: float = 0.0
    item_count: int = 0
    strategy: str = "auto"
    max_depth: int = 0
    extra: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def for_build(
        cls,
        items: list[SelectableItem],
        *,
        strategy: str = "auto",
        max_depth: int = 0,
        seed: int | None = None,
        engine_versions: dict[str, str] | None = None,
        extra: dict[str, Any] | None = None,
        timestamp: float | None = None,
    ) -> GraphManifest:
        """Construct a manifest for a freshly built graph.

        Args:
            items: The catalog items used in the build.
            strategy: Tree-building strategy that produced the graph.
            max_depth: Maximum depth of the resulting graph.
            seed: Optional seed used by stochastic engine slots.
            engine_versions: Per-slot engine version map.
            extra: Free-form additional metadata.
            timestamp: Override the build timestamp (mainly for tests).
                If ``None``, ``time.time()`` is used.

        Returns:
            A populated :class:`GraphManifest`.
        """
        return cls(
            manifest_version=MANIFEST_VERSION,
            build_hash=compute_catalog_hash(items),
            seed=seed,
            engine_versions=dict(engine_versions or {}),
            timestamp=time.time() if timestamp is None else float(timestamp),
            item_count=len(items),
            strategy=strategy,
            max_depth=int(max_depth),
            extra=dict(extra or {}),
        )

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "manifest_version": self.manifest_version,
            "build_hash": self.build_hash,
            "seed": self.seed,
            "engine_versions": dict(self.engine_versions),
            "timestamp": self.timestamp,
            "item_count": self.item_count,
            "strategy": self.strategy,
            "max_depth": self.max_depth,
            "extra": dict(self.extra),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GraphManifest:
        """Deserialise from a JSON-compatible dict."""
        seed_raw = data.get("seed")
        seed = int(seed_raw) if seed_raw is not None else None
        return cls(
            manifest_version=int(data.get("manifest_version", MANIFEST_VERSION)),
            build_hash=str(data.get("build_hash", "")),
            seed=seed,
            engine_versions=dict(data.get("engine_versions", {})),
            timestamp=float(data.get("timestamp", 0.0)),
            item_count=int(data.get("item_count", 0)),
            strategy=str(data.get("strategy", "auto")),
            max_depth=int(data.get("max_depth", 0)),
            extra=dict(data.get("extra", {})),
        )

    def matches_catalog(self, items: list[SelectableItem]) -> bool:
        """Return ``True`` if *items* hash to :attr:`build_hash`.

        Used by :class:`TreeBuilder` to decide whether a cached graph can
        be reused for a new input list.

        Args:
            items: Candidate catalog items.

        Returns:
            ``True`` when the items would produce the same build hash.
        """
        return bool(self.build_hash) and compute_catalog_hash(items) == self.build_hash

"""Vector index over MCP tool catalogs (issue #387).

Builds an embedding index over the canonical text of every
:class:`~contextweaver.types.SelectableItem` in a catalog so operators can
run semantic queries ("which tools deal with invoices?") and detect
near-duplicate tools across upstream servers.

The index is backend-agnostic: any
:class:`~contextweaver.protocols.EmbeddingBackend` works, including the
stdlib-only deterministic
:class:`~contextweaver.extras.embeddings_hashing.HashingEmbeddingBackend`.
When the backend is deterministic the whole index is deterministic —
items are stored in sorted-id order and every ranking tie-breaks by id.
Vectors are plain ``list[float]``; no numpy leaks into this module
(mirroring :mod:`contextweaver.extras.embeddings`).

Sync-only pure computation per the ``routing/`` path convention.
"""

from __future__ import annotations

import hashlib
from typing import TYPE_CHECKING

from contextweaver._utils import tokenize
from contextweaver.exceptions import ConfigError

if TYPE_CHECKING:
    from contextweaver.protocols import EmbeddingBackend
    from contextweaver.types import SelectableItem

#: Labeled canonical-text sections in their stable order.  The order doubles
#: as the deterministic tie-break for :meth:`VectorIndex.query` evidence.
SECTION_ORDER: tuple[str, ...] = ("name", "description", "schema", "metadata")


def _schema_section(item: SelectableItem) -> str:
    """Render the sorted args/output schema properties as one labeled line."""
    parts: list[str] = []
    props = item.args_schema.get("properties", {})
    if isinstance(props, dict):
        for prop_name in sorted(props):
            spec = props[prop_name]
            desc = spec.get("description", "") if isinstance(spec, dict) else ""
            parts.append(f"{prop_name}: {desc}" if desc else str(prop_name))
    out_props = (item.output_schema or {}).get("properties", {})
    if isinstance(out_props, dict) and out_props:
        parts.append("output: " + ", ".join(sorted(out_props)))
    return "; ".join(parts)


def _metadata_section(item: SelectableItem) -> str:
    """Render examples, tags, namespace, and inventory metadata as one line.

    Inventory fields (owner / domain / lifecycle) are read defensively from
    ``item.metadata["_contextweaver"]["inventory"]`` — missing or non-dict
    values simply contribute nothing.
    """
    parts: list[str] = []
    if item.examples:
        parts.append("examples: " + "; ".join(sorted(item.examples)))
    if item.tags:
        parts.append("tags: " + ", ".join(sorted(item.tags)))
    if item.namespace:
        parts.append(f"namespace: {item.namespace}")
    cw_meta = item.metadata.get("_contextweaver", {})
    inventory = cw_meta.get("inventory", {}) if isinstance(cw_meta, dict) else {}
    if isinstance(inventory, dict):
        for key in ("owner", "domain", "lifecycle"):
            value = inventory.get(key)
            if isinstance(value, str) and value:
                parts.append(f"{key}: {value}")
    return "; ".join(parts)


def _section_texts(item: SelectableItem) -> dict[str, str]:
    """Return the four labeled canonical sections for *item*."""
    return {
        "name": item.name,
        "description": item.description,
        "schema": _schema_section(item),
        "metadata": _metadata_section(item),
    }


def canonical_tool_text(item: SelectableItem) -> str:
    """Return the deterministic canonical embedding text for *item*.

    One labeled line per section in :data:`SECTION_ORDER` (``name:``,
    ``description:``, ``schema:``, ``metadata:``) with all multi-valued
    fields sorted, so identical items always produce identical text and
    retrieval evidence stays explainable per section.

    Args:
        item: The catalog item to canonicalise.

    Returns:
        A stable multi-line string, one ``label: content`` line per section.
    """
    sections = _section_texts(item)
    return "\n".join(f"{label}: {sections[label]}" for label in SECTION_ORDER)


def _text_hash(text: str) -> str:
    """Content hash used by :meth:`VectorIndex.refresh` change detection."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _sorted_unique(items: list[SelectableItem]) -> list[SelectableItem]:
    """Sort *items* by id, raising :class:`ConfigError` on duplicate ids."""
    ordered = sorted(items, key=lambda it: it.id)
    for prev, cur in zip(ordered, ordered[1:], strict=False):
        if prev.id == cur.id:
            raise ConfigError(f"duplicate item id in catalog: {cur.id!r}")
    return ordered


class VectorIndex:
    """Embedding index over a tool catalog's canonical texts.

    Items are indexed in sorted-id order so builds are deterministic
    whenever the backend is.  :meth:`refresh` re-embeds only entries whose
    canonical text changed (compared via content hash), keeping unchanged
    vectors byte-identical.

    Args:
        backend: Any :class:`~contextweaver.protocols.EmbeddingBackend`.
            Use :class:`~contextweaver.extras.embeddings_hashing.HashingEmbeddingBackend`
            for a dependency-free deterministic index.
    """

    def __init__(self, backend: EmbeddingBackend) -> None:
        self._backend = backend
        self._ids: list[str] = []
        self._vectors: list[list[float]] = []
        self._hashes: dict[str, str] = {}
        self._sections: dict[str, dict[str, str]] = {}

    @property
    def item_ids(self) -> list[str]:
        """The indexed item ids in sorted order (copy)."""
        return list(self._ids)

    def build(self, items: list[SelectableItem]) -> None:
        """Embed the canonical text of every item in *items* (full rebuild).

        Args:
            items: Catalog items; indexed in sorted-id order regardless of
                input order.

        Raises:
            ConfigError: If *items* contains duplicate ids.
        """
        ordered = _sorted_unique(items)
        texts = [canonical_tool_text(it) for it in ordered]
        self._ids = [it.id for it in ordered]
        self._vectors = self._backend.embed(texts)
        self._hashes = {it.id: _text_hash(t) for it, t in zip(ordered, texts, strict=True)}
        self._sections = {it.id: _section_texts(it) for it in ordered}

    def refresh(self, items: list[SelectableItem]) -> int:
        """Rebuild the index over *items*, re-embedding only changed entries.

        Entries whose canonical text hash is unchanged keep their existing
        vector; new or changed entries are embedded in one batch; entries
        absent from *items* are dropped.

        Args:
            items: The new full catalog.

        Returns:
            The number of entries that were (re-)embedded.

        Raises:
            ConfigError: If *items* contains duplicate ids.
        """
        ordered = _sorted_unique(items)
        old_vectors = dict(zip(self._ids, self._vectors, strict=True))
        texts = {it.id: canonical_tool_text(it) for it in ordered}
        hashes = {iid: _text_hash(text) for iid, text in texts.items()}
        changed = [it for it in ordered if self._hashes.get(it.id) != hashes[it.id]]
        new_vectors = self._backend.embed([texts[it.id] for it in changed])
        vec_by_id = {it.id: vec for it, vec in zip(changed, new_vectors, strict=True)}
        self._ids = [it.id for it in ordered]
        self._vectors = [
            vec_by_id[it.id] if it.id in vec_by_id else old_vectors[it.id] for it in ordered
        ]
        self._hashes = hashes
        self._sections = {it.id: _section_texts(it) for it in ordered}
        return len(changed)

    def query(self, text: str, top_k: int) -> list[tuple[str, float, str]]:
        """Return the *top_k* most similar items to *text*.

        Args:
            text: Free-text query.  Blank queries return an empty list.
            top_k: Maximum number of results; ``<= 0`` returns an empty list.

        Returns:
            ``(item_id, score, evidence)`` tuples sorted by score descending
            (ties broken by id).  ``evidence`` names the labeled section
            (``"name"`` / ``"description"`` / ``"schema"`` / ``"metadata"``)
            sharing the most query tokens, tie-broken by
            :data:`SECTION_ORDER`; ``"semantic"`` when no section shares any
            token with the query.
        """
        if not self._ids or top_k <= 0 or not text.strip():
            return []
        query_vecs = self._backend.embed([text])
        scores = self._backend.similarity(query_vecs[0], self._vectors)
        ranked = sorted(zip(self._ids, scores, strict=True), key=lambda pair: (-pair[1], pair[0]))
        query_tokens = tokenize(text)
        return [
            (item_id, float(score), self._evidence(item_id, query_tokens))
            for item_id, score in ranked[:top_k]
        ]

    def duplicates(self, threshold: float = 0.92) -> list[tuple[str, str, float]]:
        """Return near-duplicate item pairs by cosine similarity.

        Args:
            threshold: Minimum similarity for a pair to count (default 0.92).
                Must be in ``[0.0, 1.0]``.

        Returns:
            ``(id_a, id_b, score)`` tuples with ``id_a < id_b``, sorted by
            ``(id_a, id_b)`` for determinism.

        Raises:
            ConfigError: If *threshold* is outside ``[0.0, 1.0]``.
        """
        if not 0.0 <= threshold <= 1.0:
            raise ConfigError(f"threshold must be in [0.0, 1.0], got {threshold}")
        pairs: list[tuple[str, str, float]] = []
        for i in range(len(self._ids)):
            sims = self._backend.similarity(self._vectors[i], self._vectors[i + 1 :])
            for offset, sim in enumerate(sims, start=i + 1):
                if sim >= threshold:
                    id_a, id_b = sorted((self._ids[i], self._ids[offset]))
                    pairs.append((id_a, id_b, float(sim)))
        pairs.sort(key=lambda pair: (pair[0], pair[1]))
        return pairs

    def _evidence(self, item_id: str, query_tokens: set[str]) -> str:
        """Return the labeled section sharing the most tokens with the query."""
        best_label = "semantic"
        best_overlap = 0
        for label in SECTION_ORDER:
            overlap = len(query_tokens & tokenize(self._sections[item_id][label]))
            if overlap > best_overlap:
                best_overlap, best_label = overlap, label
        return best_label


__all__ = ["SECTION_ORDER", "VectorIndex", "canonical_tool_text"]

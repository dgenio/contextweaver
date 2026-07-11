"""Catalog diff with routing-impact analysis (issue #514).

:func:`diff_catalogs` compares two :class:`~contextweaver.types.SelectableItem`
catalogs field-by-field (via ``to_dict``) into a :class:`CatalogDiff` anchored
by :func:`~contextweaver.routing.manifest.compute_catalog_hash`.
:func:`routing_impact` builds a Router over each catalog (the same construction
as :mod:`contextweaver.eval.whatif`), replays probe queries, and reports top-1
flips plus recall@k via :func:`contextweaver.eval.metrics.recall_at_k`.
:func:`suggest_probes` derives heuristic (not gold) probes from item names and
descriptions.  Everything is pure and deterministic.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from contextweaver.eval.metrics import recall_at_k
from contextweaver.exceptions import ConfigError
from contextweaver.routing.manifest import compute_catalog_hash
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import SelectableItem

CATALOG_DIFF_VERSION: int = 1
ROUTING_IMPACT_VERSION: int = 1

MAX_EXAMPLES: int = 10  #: Cap on :class:`RoutingImpact`'s ``examples`` list.


@dataclass
class CatalogDiff:
    """Field-level difference between two catalogs (issue #514).

    Attributes:
        added: Sorted ids present only in the *after* catalog.
        removed: Sorted ids present only in the *before* catalog.
        changed: One ``{"id", "fields"}`` entry per common id, sorted by id;
            ``fields`` is the sorted list of differing ``to_dict()`` field names.
        hash_before / hash_after: ``compute_catalog_hash`` of each catalog.
    """

    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    changed: list[dict[str, Any]] = field(default_factory=list)
    hash_before: str = ""
    hash_after: str = ""

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "version": CATALOG_DIFF_VERSION,
            "added": list(self.added),
            "removed": list(self.removed),
            "changed": [
                {"id": entry["id"], "fields": list(entry["fields"])} for entry in self.changed
            ],
            "hash_before": self.hash_before,
            "hash_after": self.hash_after,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CatalogDiff:
        """Deserialise from a dict produced by :meth:`to_dict`."""
        return cls(
            added=[str(iid) for iid in data.get("added", [])],
            removed=[str(iid) for iid in data.get("removed", [])],
            changed=[
                {"id": str(entry["id"]), "fields": [str(name) for name in entry.get("fields", [])]}
                for entry in data.get("changed", [])
            ],
            hash_before=str(data.get("hash_before", "")),
            hash_after=str(data.get("hash_after", "")),
        )

    def render_markdown(self) -> str:
        """Render the diff as deterministic Markdown."""
        lines = [
            "# Catalog Diff",
            "",
            f"- Hash: `{self.hash_before}` -> `{self.hash_after}`",
            f"- Added: {len(self.added)}, removed: {len(self.removed)}, "
            f"changed: {len(self.changed)}",
        ]
        for title, ids in (("Added", self.added), ("Removed", self.removed)):
            lines += ["", f"## {title}", ""]
            lines += [f"- `{iid}`" for iid in ids] or ["- None"]
        lines += ["", "## Changed", ""]
        entries = [f"- `{e['id']}`: {', '.join(e['fields'])}" for e in self.changed]
        lines += entries or ["- None"]
        return "\n".join(lines) + "\n"


def diff_catalogs(before: list[SelectableItem], after: list[SelectableItem]) -> CatalogDiff:
    """Diff two catalogs by id and ``to_dict()`` field payloads.

    Args:
        before: The baseline catalog (never mutated).
        after: The candidate catalog (never mutated).

    Raises:
        ConfigError: If either catalog contains duplicate ids.
    """
    before_map = _by_id(before, "before")
    after_map = _by_id(after, "after")
    changed: list[dict[str, Any]] = []
    for item_id in sorted(set(before_map) & set(after_map)):
        old, new = before_map[item_id].to_dict(), after_map[item_id].to_dict()
        fields_changed = sorted(key for key in set(old) | set(new) if old.get(key) != new.get(key))
        if fields_changed:
            changed.append({"id": item_id, "fields": fields_changed})
    return CatalogDiff(
        added=sorted(set(after_map) - set(before_map)),
        removed=sorted(set(before_map) - set(after_map)),
        changed=changed,
        hash_before=compute_catalog_hash(before),
        hash_after=compute_catalog_hash(after),
    )


@dataclass
class RoutingImpact:
    """Measured routing impact of a catalog change (issue #514).

    Attributes:
        probes_total: Number of probe queries replayed.
        top1_changed: Probes whose top-1 candidate id differed across catalogs.
        examples: Up to :data:`MAX_EXAMPLES` flip examples, in probe order
            (``{"query", "before_top1", "after_top1"}``).
        recall_before / recall_after: Mean recall@`top_k` over probes carrying
            an expected id; ``None`` when no probe does.
        top_k: Rank cutoff used for recall and candidate lists.
    """

    probes_total: int = 0
    top1_changed: int = 0
    examples: list[dict[str, str]] = field(default_factory=list)
    recall_before: float | None = None
    recall_after: float | None = None
    top_k: int = 5

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "version": ROUTING_IMPACT_VERSION,
            "probes_total": self.probes_total,
            "top1_changed": self.top1_changed,
            "examples": [dict(example) for example in self.examples],
            "recall_before": self.recall_before,
            "recall_after": self.recall_after,
            "top_k": self.top_k,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> RoutingImpact:
        """Deserialise from a dict produced by :meth:`to_dict`."""
        recall_before = data.get("recall_before")
        recall_after = data.get("recall_after")
        return cls(
            probes_total=int(data.get("probes_total", 0)),
            top1_changed=int(data.get("top1_changed", 0)),
            examples=[dict(example) for example in data.get("examples", [])],
            recall_before=float(recall_before) if recall_before is not None else None,
            recall_after=float(recall_after) if recall_after is not None else None,
            top_k=int(data.get("top_k", 5)),
        )

    def render_markdown(self) -> str:
        """Render the impact report as deterministic Markdown."""
        before = f"{self.recall_before:.4f}" if self.recall_before is not None else "n/a"
        after = f"{self.recall_after:.4f}" if self.recall_after is not None else "n/a"
        lines = [
            "# Routing Impact",
            "",
            f"- Probes: {self.probes_total}",
            f"- Top-1 changed: {self.top1_changed}",
            f"- Recall@{self.top_k}: {before} -> {after}",
            "",
            "## Top-1 flips",
            "",
        ]
        entries = [
            f"- {e['query']!r}: `{e['before_top1'] or '(none)'}` -> `{e['after_top1'] or '(none)'}`"
            for e in self.examples
        ]
        lines += entries or ["- None"]
        return "\n".join(lines) + "\n"


def routing_impact(
    before: list[SelectableItem],
    after: list[SelectableItem],
    probes: list[tuple[str, str | None]],
    *,
    top_k: int = 5,
) -> RoutingImpact:
    """Replay *probes* against Routers built over *before* and *after*.

    Args:
        before: The baseline catalog.
        after: The candidate catalog.
        probes: ``(query, expected_tool_id)`` pairs; ``expected_tool_id`` may
            be ``None`` for probes that only measure top-1 stability. Expected
            ids removed by the change count as recall misses — the signal.
        top_k: Rank cutoff for the candidate lists and recall@k.

    Raises:
        ConfigError: If either catalog or *probes* is empty, or *top_k* < 1.
    """
    if not before or not after:
        raise ConfigError("routing_impact() requires non-empty before and after catalogs")
    if not probes:
        raise ConfigError("routing_impact() requires at least one probe query")
    if top_k < 1:
        raise ConfigError("routing_impact() requires top_k >= 1")
    router_before, router_after = _build_router(before, top_k), _build_router(after, top_k)
    changed = 0
    examples: list[dict[str, str]] = []
    recalls_before: list[float] = []
    recalls_after: list[float] = []
    for query, expected in probes:
        ids_before = router_before.route(query).candidate_ids
        ids_after = router_after.route(query).candidate_ids
        t1_before = ids_before[0] if ids_before else ""
        t1_after = ids_after[0] if ids_after else ""
        changed += t1_before != t1_after
        if t1_before != t1_after and len(examples) < MAX_EXAMPLES:
            examples.append({"query": query, "before_top1": t1_before, "after_top1": t1_after})
        if expected is not None:
            recalls_before.append(recall_at_k(ids_before, [expected], top_k))
            recalls_after.append(recall_at_k(ids_after, [expected], top_k))
    return RoutingImpact(
        probes_total=len(probes),
        top1_changed=changed,
        examples=examples,
        recall_before=_mean(recalls_before),
        recall_after=_mean(recalls_after),
        top_k=top_k,
    )


def suggest_probes(items: list[SelectableItem], n: int = 20) -> list[tuple[str, str]]:
    """Derive up to *n* deterministic ``(query, expected_id)`` probes from *items*.

    Heuristic, not gold data: each query is the item's name (separators
    flattened) plus its first six description words, with the item's id as
    the expected answer. Items are visited in id order — the same catalog
    always yields the same probes; empty queries are skipped.

    Args:
        items: Catalog items to derive probes from.
        n: Maximum number of probes to return.

    Raises:
        ConfigError: If *n* < 1.
    """
    if n < 1:
        raise ConfigError("suggest_probes() requires n >= 1")
    probes: list[tuple[str, str]] = []
    for item in sorted(items, key=lambda it: it.id):
        name_part = item.name.replace("_", " ").replace("-", " ").replace(".", " ")
        query = " ".join((name_part + " " + " ".join(item.description.split()[:6])).lower().split())
        if query:
            probes.append((query, item.id))
        if len(probes) == n:
            break
    return probes


def _by_id(items: list[SelectableItem], label: str) -> dict[str, SelectableItem]:
    """Index *items* by id, rejecting duplicates."""
    mapping: dict[str, SelectableItem] = {}
    for item in items:
        if item.id in mapping:
            raise ConfigError(f"duplicate item id {item.id!r} in {label} catalog")
        mapping[item.id] = item
    return mapping


def _build_router(items: list[SelectableItem], top_k: int) -> Router:
    """Build a Router over *items* the same way ``eval/whatif.py`` does."""
    graph = TreeBuilder(max_children=20).build(items)
    return Router(graph, items=list(items), top_k=top_k)


def _mean(values: list[float]) -> float | None:
    """Mean of *values* rounded to 4 places; ``None`` for an empty list."""
    return round(sum(values) / len(values), 4) if values else None


__all__ = [
    "CATALOG_DIFF_VERSION",
    "MAX_EXAMPLES",
    "ROUTING_IMPACT_VERSION",
    "CatalogDiff",
    "RoutingImpact",
    "diff_catalogs",
    "routing_impact",
    "suggest_probes",
]

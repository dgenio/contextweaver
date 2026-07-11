"""Collision and duplicate-capability analyzer for tool catalogs (issue #381).

Large multi-upstream catalogs accumulate near-duplicate tools: the same bare
name registered by two servers, camelCase/snake_case variants of one
capability, or two tools whose descriptions and schemas describe the same
operation.  Duplicates waste routing budget and split relevance scores, so
:func:`analyze_collisions` surfaces them as a deterministic
:class:`CollisionReport` (mirroring :class:`~contextweaver.routing.normalizer.
NormalizationReport`) that CI or an operator can act on.

Four finding kinds:

- ``exact_name`` — the same bare ``name`` registered in different namespaces.
- ``near_name`` — normalized-name similarity (lowercase, underscores/hyphens
  stripped, :class:`difflib.SequenceMatcher` ratio) at or above threshold;
  pairs already flagged ``exact_name`` are suppressed.
- ``similar_description`` — Jaccard similarity of tokenized descriptions at or
  above threshold (empty descriptions are skipped).  Similarity primitives come
  from :mod:`contextweaver._utils` — the single source of truth.
- ``similar_schema`` — Jaccard similarity over ``args_schema`` property-name
  sets, computed only when both sides declare at least two properties (tiny
  schemas overlap by chance, so they are skipped for false-positive
  suppression).

Recommendations are heuristic and read ``lifecycle`` / ``business_domain``
defensively as plain values from ``item.metadata["_contextweaver"]
["inventory"]`` (the issue #377 seam; no typed inventory import — the
coordinator may tighten this later).  Analysis is pure and deterministic:
items are processed in id order and findings sort by ``(kind, first id)``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from difflib import SequenceMatcher
from itertools import combinations
from typing import Any, Literal, get_args

from contextweaver._utils import jaccard, tokenize
from contextweaver.exceptions import ConfigError
from contextweaver.types import SelectableItem

#: The category of a single collision finding.
FindingKind = Literal["exact_name", "near_name", "similar_description", "similar_schema"]

#: Suggested operator action for a finding.
Recommendation = Literal["rename", "namespace", "deprecate", "consolidate", "review"]

#: Valid finding kinds (report order is alphabetical), for validation.
FINDING_KINDS: tuple[FindingKind, ...] = get_args(FindingKind)

#: Valid recommendations, for validation.
RECOMMENDATIONS: tuple[Recommendation, ...] = get_args(Recommendation)


def _inventory_str(item: SelectableItem, key: str) -> str | None:
    """Defensively read a string inventory field from *item*'s metadata (#377 seam)."""
    meta = item.metadata.get("_contextweaver")
    if not isinstance(meta, dict):
        return None
    inventory = meta.get("inventory")
    if not isinstance(inventory, dict):
        return None
    value = inventory.get(key)
    return value if isinstance(value, str) else None


def _normalize_name(name: str) -> str:
    """Lowercase *name* and strip underscore/hyphen separators for comparison."""
    return name.lower().replace("_", "").replace("-", "")


def _schema_properties(item: SelectableItem) -> set[str]:
    """Return the set of ``args_schema`` property names (empty when malformed)."""
    properties = item.args_schema.get("properties")
    return set(properties) if isinstance(properties, dict) else set()


def _recommend(kind: FindingKind, items: list[SelectableItem]) -> Recommendation:
    """Pick a suggested action for a finding over *items* (ordered heuristic)."""
    if kind == "exact_name":
        return "namespace"
    if kind == "near_name":
        return "rename"
    if kind == "similar_description":
        domains = [_inventory_str(item, "business_domain") for item in items]
        if domains[0] is not None and all(domain == domains[0] for domain in domains):
            return "consolidate"
    lifecycles = {_inventory_str(item, "lifecycle") for item in items}
    if {"deprecated", "active"} <= lifecycles:
        return "deprecate"
    return "review"


def _pair_finding(
    kind: FindingKind, a: SelectableItem, b: SelectableItem, score: float, evidence: str
) -> CollisionFinding:
    """Build a two-item finding (ids pre-sorted by the caller) with its recommendation."""
    return CollisionFinding(
        kind=kind,
        item_ids=[a.id, b.id],
        score=round(score, 4),
        recommendation=_recommend(kind, [a, b]),
        evidence=evidence,
    )


@dataclass
class CollisionFinding:
    """One detected collision between two or more catalog items.

    Attributes:
        kind: The finding category (see :data:`FINDING_KINDS`).
        item_ids: Sorted ids of the colliding items.
        score: Similarity in ``[0.0, 1.0]`` (``1.0`` for exact collisions).
        recommendation: Suggested operator action (see :data:`RECOMMENDATIONS`).
        evidence: Short human-readable justification for the finding.
    """

    kind: FindingKind
    item_ids: list[str]
    score: float
    recommendation: Recommendation
    evidence: str

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "kind": self.kind,
            "item_ids": list(self.item_ids),
            "score": self.score,
            "recommendation": self.recommendation,
            "evidence": self.evidence,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CollisionFinding:
        """Deserialise from a dict, validating the enum-valued fields."""
        kind = data.get("kind")
        if kind not in FINDING_KINDS:
            raise ConfigError(f"CollisionFinding.kind must be one of {FINDING_KINDS}, got {kind!r}")
        recommendation = data.get("recommendation")
        if recommendation not in RECOMMENDATIONS:
            raise ConfigError(
                f"CollisionFinding.recommendation must be one of {RECOMMENDATIONS},"
                f" got {recommendation!r}"
            )
        return cls(
            kind=kind,
            item_ids=[str(item_id) for item_id in data.get("item_ids", [])],
            score=float(data.get("score", 0.0)),
            recommendation=recommendation,
            evidence=str(data.get("evidence", "")),
        )


@dataclass
class CollisionReport:
    """Deterministic result of :func:`analyze_collisions`.

    Attributes:
        findings: Findings sorted by ``(kind, first item id, item ids)``.
        counts: Findings per kind — every kind present, zero-filled.
    """

    findings: list[CollisionFinding] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=lambda: dict.fromkeys(FINDING_KINDS, 0))

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "findings": [finding.to_dict() for finding in self.findings],
            "counts": {kind: self.counts.get(kind, 0) for kind in FINDING_KINDS},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> CollisionReport:
        """Deserialise from a dict produced by :meth:`to_dict`."""
        return cls(
            findings=[CollisionFinding.from_dict(raw) for raw in data.get("findings", [])],
            counts={kind: int(data.get("counts", {}).get(kind, 0)) for kind in FINDING_KINDS},
        )

    def render_markdown(self) -> str:
        """Render a deterministic Markdown summary of the report."""
        lines = ["# Catalog Collision Report", "", "## Counts", ""]
        for kind in FINDING_KINDS:
            lines.append(f"- `{kind}`: {self.counts.get(kind, 0)}")
        lines += ["", f"Total findings: {len(self.findings)}", ""]
        if self.findings:
            lines += [
                "## Findings",
                "",
                "| kind | items | score | recommendation | evidence |",
                "|---|---|---|---|---|",
            ]
            for finding in self.findings:
                cells = (
                    finding.kind,
                    ", ".join(finding.item_ids),
                    f"{finding.score:.2f}",
                    finding.recommendation,
                    finding.evidence,
                )
                lines.append("| " + " | ".join(cell.replace("|", "\\|") for cell in cells) + " |")
            lines.append("")
        return "\n".join(lines)


def analyze_collisions(
    items: list[SelectableItem],
    *,
    near_name_threshold: float = 0.84,
    description_threshold: float = 0.8,
    schema_threshold: float = 0.8,
) -> CollisionReport:
    """Detect name, description, and schema collisions across *items*.

    Args:
        items: Catalog items to analyze (never mutated).
        near_name_threshold: Minimum :class:`difflib.SequenceMatcher` ratio of
            the normalized names for a ``near_name`` finding.
        description_threshold: Minimum Jaccard similarity of tokenized
            descriptions for a ``similar_description`` finding.
        schema_threshold: Minimum Jaccard similarity of ``args_schema``
            property-name sets for a ``similar_schema`` finding.

    Returns:
        A :class:`CollisionReport` with findings sorted by
        ``(kind, first item id, item ids)`` and per-kind counts.
    """
    ordered = sorted(items, key=lambda item: item.id)
    findings: list[CollisionFinding] = []

    # exact_name: the same bare name registered in >=2 namespaces.
    by_name: dict[str, list[SelectableItem]] = {}
    for item in ordered:
        by_name.setdefault(item.name, []).append(item)
    exact_pairs: set[tuple[str, str]] = set()
    for name in sorted(by_name):
        group = by_name[name]
        namespaces = sorted({item.namespace for item in group})
        if len(group) < 2 or len(namespaces) < 2:
            continue
        ids = sorted(item.id for item in group)
        findings.append(
            CollisionFinding(
                kind="exact_name",
                item_ids=ids,
                score=1.0,
                recommendation=_recommend("exact_name", group),
                evidence=f"name {name!r} appears in namespaces: {', '.join(namespaces)}",
            )
        )
        exact_pairs.update(combinations(ids, 2))

    # Pairwise passes; ``(a.id, b.id)`` is always sorted since ``ordered`` is.
    for a, b in combinations(ordered, 2):
        # near_name: normalized-name similarity; exact pairs are suppressed.
        norm_a, norm_b = _normalize_name(a.name), _normalize_name(b.name)
        if norm_a and norm_b and (a.id, b.id) not in exact_pairs:
            ratio = SequenceMatcher(None, norm_a, norm_b).ratio()
            if ratio >= near_name_threshold:
                evidence = (
                    f"names {a.name!r} / {b.name!r} normalize to"
                    f" {norm_a!r} / {norm_b!r} (ratio {ratio:.2f})"
                )
                findings.append(_pair_finding("near_name", a, b, ratio, evidence))

        # similar_description: token-set Jaccard; empty descriptions skipped.
        if a.description.strip() and b.description.strip():
            similarity = jaccard(tokenize(a.description), tokenize(b.description))
            if similarity >= description_threshold:
                evidence = f"description Jaccard {similarity:.2f}"
                findings.append(_pair_finding("similar_description", a, b, similarity, evidence))

        # similar_schema: property-name Jaccard; both sides need >=2 properties.
        props_a, props_b = _schema_properties(a), _schema_properties(b)
        if len(props_a) >= 2 and len(props_b) >= 2:
            similarity = jaccard(props_a, props_b)
            if similarity >= schema_threshold:
                shared = ", ".join(sorted(props_a & props_b))
                evidence = f"schema Jaccard {similarity:.2f} (shared: {shared})"
                findings.append(_pair_finding("similar_schema", a, b, similarity, evidence))

    findings.sort(key=lambda finding: (finding.kind, finding.item_ids[0], finding.item_ids))
    counts: dict[str, int] = dict.fromkeys(FINDING_KINDS, 0)
    for finding in findings:
        counts[finding.kind] += 1
    return CollisionReport(findings=findings, counts=counts)


__all__ = [
    "FINDING_KINDS",
    "RECOMMENDATIONS",
    "CollisionFinding",
    "CollisionReport",
    "analyze_collisions",
]

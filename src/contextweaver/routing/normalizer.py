"""Catalog metadata hygiene for the contextweaver Routing Engine.

External catalog sources — MCP server discovery responses, A2A skill
cards, hand-curated JSON exports — produce :class:`SelectableItem`
objects with inconsistent metadata: missing descriptions, duplicate or
casing-variant tags, irregular IDs.  The :class:`CatalogNormalizer`
applies deterministic hygiene before items reach
:class:`~contextweaver.routing.tree.TreeBuilder`, where metadata
quality directly influences routing scores.

Normalization rules (issue #44):

* **Tags**: deduplicate case-insensitively, lower-case, sort, and strip
  whitespace.  An item with tags ``["Email", "email", "  EMAIL "]``
  becomes ``["email"]``.
* **Descriptions**: collapse runs of whitespace into a single space and
  strip leading/trailing whitespace.  Empty descriptions are filled
  from the item name.
* **Names**: strip outer whitespace; keep internal capitalisation.
* **IDs**: validated only — a normalizer never rewrites an ID since
  IDs are user-facing references the catalog producer is expected to
  control.
* **Namespaces**: stripped of trailing ``.`` and whitespace.

The normalizer is purely additive: it does not drop items, does not
mutate input objects, and produces a fresh list of cloned items.
:class:`NormalizationReport` summarises what was changed for telemetry
and CI use.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from contextweaver.exceptions import CatalogError
from contextweaver.types import SelectableItem

#: Regex used to collapse runs of whitespace.
_WS_RUN_RE = re.compile(r"\s+")


@dataclass
class NormalizationReport:
    """Summary of changes applied by :meth:`CatalogNormalizer.normalize`.

    Attributes:
        items_processed: Total items in the input list.
        tag_dedup_count: Number of items whose tag list shrank after
            case-insensitive deduplication.
        description_filled_count: Number of items whose empty
            description was filled from the name.
        whitespace_normalized_count: Number of items whose name,
            description, or namespace had whitespace adjustments.
        invalid_ids: IDs that failed validation (when ``strict=False``;
            empty in the default lenient mode is ``[]``).  In
            ``strict=True`` mode the normalizer raises before returning.
    """

    items_processed: int = 0
    tag_dedup_count: int = 0
    description_filled_count: int = 0
    whitespace_normalized_count: int = 0
    invalid_ids: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "items_processed": self.items_processed,
            "tag_dedup_count": self.tag_dedup_count,
            "description_filled_count": self.description_filled_count,
            "whitespace_normalized_count": self.whitespace_normalized_count,
            "invalid_ids": list(self.invalid_ids),
        }

    @property
    def changed_count(self) -> int:
        """Total number of items that received any change."""
        return (
            self.tag_dedup_count + self.description_filled_count + self.whitespace_normalized_count
        )


class CatalogNormalizer:
    """Apply deterministic metadata hygiene to a list of catalog items.

    The normalizer is stateless — its only mutable surface is its
    constructor configuration.  Call :meth:`normalize` to produce a
    cleaned list and a :class:`NormalizationReport`.

    Args:
        strict: When ``True``, raise :class:`CatalogError` on the first
            invalid id (e.g. blank id, duplicate id).  When ``False``
            (default), invalid ids are recorded in the report and the
            item is dropped from the output.
        lowercase_tags: When ``True`` (default), tags are lower-cased
            before deduplication.  When ``False``, deduplication is
            case-sensitive.
    """

    def __init__(self, *, strict: bool = False, lowercase_tags: bool = True) -> None:
        self._strict = strict
        self._lowercase_tags = lowercase_tags

    def normalize(
        self,
        items: list[SelectableItem],
    ) -> tuple[list[SelectableItem], NormalizationReport]:
        """Return a normalized copy of *items* and a change report.

        Args:
            items: Raw catalog items to clean.

        Returns:
            ``(normalized_items, report)``.  *normalized_items* is a
            fresh list of fresh :class:`SelectableItem` clones — input
            objects are never mutated.

        Raises:
            CatalogError: When ``strict=True`` and an item has an
                invalid id (blank or duplicate within the input).
        """
        report = NormalizationReport(items_processed=len(items))
        seen_ids: set[str] = set()
        out: list[SelectableItem] = []

        for item in items:
            if not item.id or not item.id.strip():
                if self._strict:
                    raise CatalogError(f"Item has empty id: {item!r}")
                report.invalid_ids.append(item.id)
                continue
            if item.id in seen_ids:
                if self._strict:
                    raise CatalogError(f"Duplicate item id: {item.id!r}")
                report.invalid_ids.append(item.id)
                continue
            seen_ids.add(item.id)

            normalized, changes = self._normalize_one(item)
            out.append(normalized)
            if changes["tags"]:
                report.tag_dedup_count += 1
            if changes["description_filled"]:
                report.description_filled_count += 1
            if changes["whitespace"]:
                report.whitespace_normalized_count += 1

        return out, report

    def _normalize_one(
        self,
        item: SelectableItem,
    ) -> tuple[SelectableItem, dict[str, bool]]:
        """Return a normalized clone of *item* plus a per-field change map."""
        changes = {"tags": False, "description_filled": False, "whitespace": False}

        # Tags: case-fold, dedupe, sort.
        original_tags = list(item.tags)
        cleaned_tags: list[str] = []
        seen: set[str] = set()
        for raw in original_tags:
            tag = raw.strip()
            if not tag:
                continue
            key = tag.lower() if self._lowercase_tags else tag
            stored = tag.lower() if self._lowercase_tags else tag
            if key in seen:
                continue
            seen.add(key)
            cleaned_tags.append(stored)
        cleaned_tags.sort()
        if cleaned_tags != original_tags:
            changes["tags"] = True

        # Whitespace normalisation on free-text fields.
        clean_name = _WS_RUN_RE.sub(" ", item.name).strip()
        clean_namespace = item.namespace.strip().rstrip(".")
        clean_desc = _WS_RUN_RE.sub(" ", item.description).strip()
        if (
            clean_name != item.name
            or clean_namespace != item.namespace
            or clean_desc != item.description
        ):
            changes["whitespace"] = True

        # Description fallback.
        if not clean_desc:
            clean_desc = clean_name
            changes["description_filled"] = True

        return (
            SelectableItem(
                id=item.id,
                kind=item.kind,
                name=clean_name,
                description=clean_desc,
                tags=cleaned_tags,
                namespace=clean_namespace,
                args_schema=dict(item.args_schema),
                output_schema=(
                    dict(item.output_schema) if item.output_schema is not None else None
                ),
                examples=list(item.examples),
                constraints=dict(item.constraints),
                side_effects=item.side_effects,
                cost_hint=item.cost_hint,
                metadata=dict(item.metadata),
            ),
            changes,
        )


__all__ = [
    "CatalogNormalizer",
    "NormalizationReport",
]

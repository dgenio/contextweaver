"""Optional LLM-assisted catalog metadata enrichment (issue #383).

Large MCP catalogs accumulate weak names, vague descriptions, and missing
governance metadata.  This module runs an *offline*, suggestion-only
enrichment pass with the operator's model of choice: each tool's compact
deterministic prompt (name, description, schema property names — never
argument values or results) goes through a caller-supplied ``call_fn``, and
the strict-JSON response becomes reviewable :class:`EnrichmentSuggestion`s.

Per the deterministic-first rubric (``docs/agent-context/model-backed-features.md``):
nothing is ever applied automatically — the output is a diff-style report for
human review; enum-bearing suggestions are validated against the issue #377
vocabularies (invalid values land in ``skipped``, never in the catalog);
calls run through :class:`~contextweaver.extras.llm_guard.GuardedCallFn` when
a guard policy is supplied; provider/model identifiers are recorded for
audit.  :func:`apply_suggestions` is the explicit, reviewed opt-in — it
applies only caller-accepted ``(tool_id, field)`` pairs it knows how to write.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from contextweaver.exceptions import ConfigError
from contextweaver.routing.catalog_metadata import (
    RISK_LEVELS,
    SIDE_EFFECT_LEVELS,
    attach_inventory,
    inventory_of,
    validate_inventory,
)

if TYPE_CHECKING:
    from contextweaver.extras.llm_guard import GuardPolicy
    from contextweaver.types import SelectableItem

#: Fields enrichment may suggest.  ``description`` edits the item itself;
#: ``business_domain``/``risk_level``/``side_effects`` target the issue #377
#: inventory namespace; the rest are advisory-only report fields.
ENRICHABLE_FIELDS: tuple[str, ...] = (
    "description",
    "business_domain",
    "risk_level",
    "side_effects",
    "aliases",
    "use_cases",
    "duplicate_of",
)

#: Fields whose suggested values must come from a fixed vocabulary.
_ENUM_FIELDS: dict[str, tuple[str, ...]] = {
    "risk_level": RISK_LEVELS,
    "side_effects": SIDE_EFFECT_LEVELS,
}

#: Fields :func:`apply_suggestions` knows how to write back.
_APPLICABLE_FIELDS: frozenset[str] = frozenset(
    {"description", "business_domain", "risk_level", "side_effects"}
)


@dataclass
class EnrichmentSuggestion:
    """One reviewable, model-produced metadata suggestion."""

    tool_id: str
    field: str
    suggested: str | list[str]
    current: str | None = None
    rationale: str = ""
    source: str = "llm_assisted"

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "tool_id": self.tool_id,
            "field": self.field,
            "suggested": self.suggested,
            "current": self.current,
            "rationale": self.rationale,
            "source": self.source,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EnrichmentSuggestion:
        """Deserialise from a JSON-compatible dict."""
        if data.get("field") not in ENRICHABLE_FIELDS:
            raise ConfigError(f"unknown enrichment field {data.get('field')!r}")
        return cls(
            tool_id=str(data["tool_id"]),
            field=str(data["field"]),
            suggested=data["suggested"],
            current=data.get("current"),
            rationale=str(data.get("rationale", "")),
            source=str(data.get("source", "llm_assisted")),
        )


@dataclass
class EnrichmentReport:
    """All suggestions from one enrichment run, plus audit metadata."""

    suggestions: list[EnrichmentSuggestion] = field(default_factory=list)
    skipped: list[tuple[str, str]] = field(default_factory=list)
    provider_metadata: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "suggestions": [s.to_dict() for s in self.suggestions],
            "skipped": [list(pair) for pair in self.skipped],
            "provider_metadata": dict(self.provider_metadata),
        }

    def render_jsonl(self) -> str:
        """Render one JSON object per suggestion (review tooling input)."""
        return "\n".join(json.dumps(s.to_dict(), sort_keys=True) for s in self.suggestions)

    def render_markdown(self) -> str:
        """Render a diff-style review table."""
        lines = [
            "# Catalog enrichment suggestions (llm_assisted — review before applying)",
            "",
            f"Provider: `{json.dumps(self.provider_metadata, sort_keys=True)}`",
            "",
            "| tool_id | field | current | suggested | rationale |",
            "|---|---|---|---|---|",
        ]
        for s in self.suggestions:
            suggested = ", ".join(s.suggested) if isinstance(s.suggested, list) else s.suggested
            cells = [s.tool_id, s.field, s.current or "—", suggested, s.rationale]
            lines.append("| " + " | ".join(c.replace("|", "\\|") for c in cells) + " |")
        if self.skipped:
            lines += ["", "## Skipped", ""]
            lines += [f"- `{tool_id}`: {reason}" for tool_id, reason in self.skipped]
        return "\n".join(lines)


def _enrichment_prompt(item: SelectableItem, fields: tuple[str, ...]) -> str:
    """Deterministic per-tool prompt — metadata only, never payload values."""
    properties = sorted((item.args_schema or {}).get("properties", {}))
    return "\n".join(
        [
            "Suggest better catalog metadata for one agent tool.",
            f"Tool name: {item.name}",
            f"Namespace: {item.namespace or '(none)'}",
            f"Description: {item.description or '(empty)'}",
            f"Schema property names: {', '.join(properties) or '(none)'}",
            f"Tags: {', '.join(item.tags) or '(none)'}",
            "",
            "Answer with strict JSON containing only these optional keys: "
            + ", ".join(fields)
            + ".",
            'risk_level must be one of ["low","medium","high"]; side_effects one of '
            '["none","read","write","destructive"]; aliases and use_cases are string arrays.',
        ]
    )


def _current_value(item: SelectableItem, field_name: str) -> str | None:
    """Return the item's current value for *field_name*, when it has one."""
    if field_name == "description":
        return item.description or None
    inventory = inventory_of(item)
    if inventory is not None and field_name in {"business_domain", "risk_level", "side_effects"}:
        value = getattr(inventory, field_name)
        return str(value) if value is not None else None
    return None


def _suggestions_from_response(
    item: SelectableItem, data: dict[str, Any], fields: tuple[str, ...]
) -> tuple[list[EnrichmentSuggestion], list[str]]:
    """Convert one parsed model response into validated suggestions."""
    suggestions: list[EnrichmentSuggestion] = []
    problems: list[str] = []
    for field_name in fields:
        if field_name not in data:
            continue
        value = data[field_name]
        if field_name in _ENUM_FIELDS:
            if value not in _ENUM_FIELDS[field_name]:
                problems.append(f"{field_name}: invalid value {value!r}")
                continue
        elif field_name in {"aliases", "use_cases"}:
            if not isinstance(value, list) or not all(isinstance(v, str) for v in value):
                problems.append(f"{field_name}: expected a string array")
                continue
        elif not isinstance(value, str) or not value.strip():
            problems.append(f"{field_name}: expected a non-empty string")
            continue
        suggestions.append(
            EnrichmentSuggestion(
                tool_id=item.id,
                field=field_name,
                suggested=value,
                current=_current_value(item, field_name),
                rationale=str(data.get("rationale", "")),
            )
        )
    return suggestions, problems


def enrich_catalog(
    items: list[SelectableItem],
    call_fn: Callable[[str], str],
    *,
    fields: tuple[str, ...] = ENRICHABLE_FIELDS,
    guard_policy: GuardPolicy | None = None,
    provider_metadata: dict[str, str] | None = None,
    max_items: int | None = None,
) -> EnrichmentReport:
    """Generate reviewable metadata suggestions for *items* — applies nothing.

    Malformed model output, invalid enum values, and guard rejections move
    the affected tool to ``report.skipped`` with a reason; the run never
    raises mid-catalog and never mutates *items*.
    """
    unknown = [f for f in fields if f not in ENRICHABLE_FIELDS]
    if unknown:
        raise ConfigError(f"unknown enrichment fields: {unknown}; allowed: {ENRICHABLE_FIELDS}")
    dispatch: Callable[[str], str] = call_fn
    if guard_policy is not None:
        from contextweaver.extras.llm_guard import GuardedCallFn

        dispatch = GuardedCallFn(call_fn, guard_policy)
    report = EnrichmentReport(provider_metadata=dict(provider_metadata or {}))
    for item in items if max_items is None else items[:max_items]:
        try:
            raw = dispatch(_enrichment_prompt(item, fields))
        except Exception as exc:
            report.skipped.append((item.id, f"call failed: {exc}"))
            continue
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as exc:
            report.skipped.append((item.id, f"malformed response: {exc}"))
            continue
        if not isinstance(data, dict):
            report.skipped.append((item.id, "malformed response: not a JSON object"))
            continue
        suggestions, problems = _suggestions_from_response(item, data, fields)
        report.suggestions.extend(suggestions)
        report.skipped.extend((item.id, problem) for problem in problems)
    return report


def apply_suggestions(
    items: list[SelectableItem],
    report: EnrichmentReport,
    *,
    accept: set[tuple[str, str]],
) -> list[SelectableItem]:
    """Apply only the reviewed, explicitly accepted suggestions.

    ``accept`` holds ``(tool_id, field)`` pairs.  Only ``description`` and the
    inventory fields (``business_domain``/``risk_level``/``side_effects``)
    are writable; accepting any other field raises :class:`ConfigError`.
    Returns a new item list — inputs are never mutated.
    """
    chosen: dict[tuple[str, str], EnrichmentSuggestion] = {
        (s.tool_id, s.field): s for s in report.suggestions if (s.tool_id, s.field) in accept
    }
    for tool_id, field_name in accept:
        if field_name not in _APPLICABLE_FIELDS:
            raise ConfigError(f"field {field_name!r} is advisory-only and cannot be applied")
        if (tool_id, field_name) not in chosen:
            raise ConfigError(f"no suggestion for ({tool_id!r}, {field_name!r}) in this report")
    out: list[SelectableItem] = []
    for item in items:
        updated = item
        inventory_updates: dict[str, Any] = {}
        for field_name in ("business_domain", "risk_level", "side_effects"):
            suggestion = chosen.get((item.id, field_name))
            if suggestion is not None:
                inventory_updates[field_name] = suggestion.suggested
        description = chosen.get((item.id, "description"))
        if description is not None or inventory_updates:
            payload = item.to_dict()
            if description is not None:
                payload["description"] = description.suggested
            updated = type(item).from_dict(payload)
            if inventory_updates:
                existing = inventory_of(item)
                merged = dict(existing.to_dict()) if existing is not None else {}
                merged.update(inventory_updates)
                updated = attach_inventory(updated, validate_inventory(merged))
        out.append(updated)
    return out


__all__ = [
    "ENRICHABLE_FIELDS",
    "EnrichmentReport",
    "EnrichmentSuggestion",
    "apply_suggestions",
    "enrich_catalog",
]

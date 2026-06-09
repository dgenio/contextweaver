"""Structured (lossless) firewall projection for contextweaver (issue #406).

The text-summarising firewall is the wrong primitive for **structured JSON
tool results** — the dominant shape for line-of-business agents (billing,
invoicing, CRM, catalog lookups).  For those, the correct reduction is
**field allow-listing / projection**: keep an allow-listed set of JSON paths
inline, offload everything else to the artifact store, and make the dropped
data retrievable by selector via the existing ``drilldown`` protocol.

This is *lossless within the chosen schema*, deterministic, and auditable —
properties text summarisation cannot provide, and it performs **no LLM call**
(pure structural transform).

Path grammar
------------

A path is a dotted sequence of object keys.  A ``[]`` suffix on a segment
expands across every element of a list and descends into the remaining path
for each element::

    result.response.invoices[].invoiceNumber
    result.response.invoices[].amount
    result.response.total

A path with no further segments keeps the matched value whole (object, array,
or scalar).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from contextweaver.exceptions import ConfigError
from contextweaver.summarize.extract import StructuredExtractor

# Sentinel distinguishing "path did not match" from a legitimately-present
# ``None`` value in the source payload.
_MISSING: Any = object()

#: Marker segment produced by :func:`parse_path` for a ``[]`` list-expansion.
_LIST = "[]"


def parse_path(path: str) -> list[str]:
    """Split a dotted allow-list *path* into key / ``[]`` segments.

    Args:
        path: A path such as ``"result.response.invoices[].amount"``.

    Returns:
        The ordered segments, e.g.
        ``["result", "response", "invoices", "[]", "amount"]``.

    Raises:
        ConfigError: If *path* is empty or whitespace-only.
    """
    cleaned = path.strip()
    if not cleaned:
        raise ConfigError("StructuredFirewall keep-path must be a non-empty string")
    segments: list[str] = []
    for part in cleaned.split("."):
        token = part
        while token.endswith(_LIST):
            key = token[: -len(_LIST)]
            if key:
                segments.append(key)
            segments.append(_LIST)
            token = ""
        if token:
            segments.append(token)
    return segments


def _extract(obj: Any, segments: list[str]) -> Any:  # noqa: ANN401 — arbitrary JSON
    """Return the pruned substructure of *obj* for one path's *segments*.

    Returns :data:`_MISSING` when the path does not resolve against *obj*.
    """
    if not segments:
        return obj
    head, rest = segments[0], segments[1:]
    if head == _LIST:
        if not isinstance(obj, list):
            return _MISSING
        out: list[Any] = []
        for element in obj:
            sub = _extract(element, rest)
            out.append(None if sub is _MISSING else sub)
        return out
    if isinstance(obj, dict) and head in obj:
        sub = _extract(obj[head], rest)
        if sub is _MISSING:
            return _MISSING
        return {head: sub}
    return _MISSING


def _merge(a: Any, b: Any) -> Any:  # noqa: ANN401 — arbitrary JSON
    """Deep-merge two pruned substructures (later allow-list paths fold in)."""
    if isinstance(a, dict) and isinstance(b, dict):
        merged = dict(a)
        for key, value in b.items():
            merged[key] = _merge(merged[key], value) if key in merged else value
        return merged
    if isinstance(a, list) and isinstance(b, list):
        return [
            _merge(a[i], b[i]) if i < len(a) and i < len(b) else (a[i] if i < len(a) else b[i])
            for i in range(max(len(a), len(b)))
        ]
    # Disjoint allow-list paths should not collide on a scalar; if they do,
    # the most-recent extraction wins deterministically.
    return b


def project(obj: Any, keep: list[str]) -> Any:  # noqa: ANN401 — arbitrary JSON
    """Project *obj* down to the allow-listed *keep* paths (lossless subset).

    Args:
        obj: The decoded JSON payload (``dict`` / ``list`` / scalar).
        keep: Allow-listed paths in :func:`parse_path` grammar.

    Returns:
        A new structure containing only the allow-listed paths, preserving the
        original nesting.  Paths that do not resolve are skipped.  Returns an
        empty ``dict`` when nothing matches.
    """
    result: Any = _MISSING
    for path in keep:
        extracted = _extract(obj, parse_path(path))
        if extracted is _MISSING:
            continue
        result = extracted if result is _MISSING else _merge(result, extracted)
    return {} if result is _MISSING else result


@dataclass
class StructuredFirewall:
    """A non-summarising firewall strategy: keep allow-listed JSON paths inline.

    Pass an instance to the single-call
    :func:`~contextweaver.context.firewall_api.compact_tool_result` facade or to
    :meth:`ContextManager.ingest_tool_result` / ``ingest_mcp_result`` (via the
    ``firewall=`` argument) to select deterministic field projection instead of
    text summarisation.  The full payload is still offloaded to the artifact
    store so dropped fields stay retrievable through ``drilldown``.

    Attributes:
        keep: Allow-listed paths (e.g.
            ``["result.response.invoices[].amount", "result.response.total"]``).
            Must be non-empty.
        max_fact_chars: Upper bound on the total characters of the derived fact
            list (forwarded to :class:`StructuredExtractor`).
    """

    keep: list[str] = field(default_factory=list)
    max_fact_chars: int = 500

    def __post_init__(self) -> None:
        """Validate the allow-list is non-empty (security-grade: fail loud)."""
        if not self.keep:
            raise ConfigError(
                "StructuredFirewall requires a non-empty keep allow-list; "
                "an empty allow-list would drop the entire payload from the prompt"
            )
        for path in self.keep:
            parse_path(path)  # validate grammar eagerly

    def compact(self, data: Any) -> tuple[Any, list[str]]:  # noqa: ANN401 — arbitrary JSON
        """Project *data* and derive a deterministic fact list.

        Args:
            data: The decoded JSON payload to project.

        Returns:
            A ``(projected, facts)`` tuple — *projected* is the lossless inline
            subset; *facts* are structured facts derived from it.
        """
        projected = project(data, self.keep)
        facts = StructuredExtractor(max_chars=self.max_fact_chars).extract(
            json.dumps(projected, sort_keys=True), {}
        )
        return projected, facts

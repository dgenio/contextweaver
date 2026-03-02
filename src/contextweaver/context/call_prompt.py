"""Call-phase prompt helpers for the contextweaver Context Engine.

Provides :func:`build_schema_header` which assembles the ``[TOOL SCHEMA]``
prompt section from a :class:`~contextweaver.envelope.HydrationResult`.
"""

from __future__ import annotations

import json
from typing import Any

from contextweaver.envelope import HydrationResult


def build_schema_header(
    hydration: HydrationResult,
    schema: dict[str, Any] | None = None,
    examples: list[str] | None = None,
) -> str:
    """Build a ``[TOOL SCHEMA]`` prompt header from hydration data.

    Assembles a deterministic, human-readable header containing the tool's
    name, description, argument schema, constraints, examples, cost hint,
    and side-effects flag.

    Args:
        hydration: The :class:`~contextweaver.envelope.HydrationResult`
            returned by :meth:`~contextweaver.routing.catalog.Catalog.hydrate`.
        schema: Override schema dict (replaces hydrated ``args_schema``
            in the header; does not skip hydration).
        examples: Override example strings (replaces hydrated examples
            in the header; does not skip hydration).

    Returns:
        A formatted prompt header string starting with ``[TOOL SCHEMA]``.
    """
    effective_schema = schema if schema is not None else hydration.args_schema
    effective_examples = examples if examples is not None else hydration.examples

    sections: list[str] = [
        f"[TOOL SCHEMA]\nTool: {hydration.item.name} ({hydration.item.id})",
        f"Description: {hydration.item.description}",
    ]
    if effective_schema:
        schema_text = json.dumps(effective_schema, indent=2, sort_keys=True)
        sections.append(f"Schema:\n{schema_text}")
    if hydration.constraints:
        constraints_text = json.dumps(hydration.constraints, indent=2, sort_keys=True)
        sections.append(f"Constraints:\n{constraints_text}")
    if effective_examples:
        ex_lines = "\n".join(f"  - {ex}" for ex in effective_examples)
        sections.append(f"Examples:\n{ex_lines}")
    if hydration.item.cost_hint > 0:
        sections.append(f"Cost hint: {hydration.item.cost_hint}")
    if hydration.item.side_effects:
        sections.append("Side effects: yes")

    return "\n".join(sections)

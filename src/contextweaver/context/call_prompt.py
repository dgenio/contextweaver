"""Call-phase prompt helpers for the contextweaver Context Engine.

Provides :func:`build_schema_header` which assembles the ``[TOOL SCHEMA]``
prompt section from a :class:`~contextweaver.envelope.HydrationResult`.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from contextweaver.envelope import HydrationResult
from contextweaver.types import Phase

if TYPE_CHECKING:
    from contextweaver.context._manager_base import _ManagerState
    from contextweaver.envelope import ContextPack
    from contextweaver.routing.catalog import Catalog


def build_schema_header(
    hydration: HydrationResult,
    schema: dict[str, Any] | None = None,
    examples: list[str] | None = None,
    constraints: dict[str, Any] | None = None,
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
        constraints: Override constraints dict (replaces hydrated
            ``constraints`` in the header; does not skip hydration).

    Returns:
        A formatted prompt header string starting with ``[TOOL SCHEMA]``.
    """
    effective_schema = schema if schema is not None else hydration.args_schema
    effective_examples = examples if examples is not None else hydration.examples
    effective_constraints = constraints if constraints is not None else hydration.constraints

    sections: list[str] = [
        f"[TOOL SCHEMA]\nTool: {hydration.item.name} ({hydration.item.id})",
        f"Description: {hydration.item.description}",
    ]
    if effective_schema:
        schema_text = json.dumps(effective_schema, indent=2, sort_keys=True)
        sections.append(f"Schema:\n{schema_text}")
    if effective_constraints:
        constraints_text = json.dumps(effective_constraints, indent=2, sort_keys=True)
        sections.append(f"Constraints:\n{constraints_text}")
    if effective_examples:
        ex_lines = "\n".join(f"  - {ex}" for ex in effective_examples)
        sections.append(f"Examples:\n{ex_lines}")
    if hydration.item.cost_hint > 0:
        sections.append(f"Cost hint: {hydration.item.cost_hint}")
    if hydration.item.side_effects:
        sections.append("Side effects: yes")

    return "\n".join(sections)


def run_call_prompt_build(
    manager: _ManagerState,
    tool_id: str,
    query: str,
    catalog: Catalog,
    schema: dict[str, Any] | None = None,
    examples: list[str] | None = None,
    constraints: dict[str, Any] | None = None,
    budget_tokens: int | None = None,
) -> ContextPack:
    """Hydrate *tool_id*, build its schema header, and run the call-phase build.

    Extracted from :meth:`ContextManager.build_call_prompt` (issue #101). Not
    public API — operates on a :class:`ContextManager`'s internals.

    Raises:
        ItemNotFoundError: If *tool_id* is not in *catalog*.
    """
    hydration = catalog.hydrate(tool_id)
    header = build_schema_header(
        hydration,
        schema=schema,
        examples=examples,
        constraints=constraints,
    )
    pack, _explanation = manager._build(
        phase=Phase.call,
        query=query,
        header=header,
        budget_tokens=budget_tokens,
    )
    return pack

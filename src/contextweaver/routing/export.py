"""Provider-native tool wire-format exporters for routed shortlists (issue #609).

After :meth:`Router.route` produces a bounded shortlist, callers wiring a
provider SDK directly need the selected tools in the provider's native
``tools`` wire shape — with the *full* argument schema, which ChoiceCards
deliberately never carry.  These exporters hydrate each routed item and emit:

- :func:`to_openai_tools` — OpenAI Chat Completions ``tools`` entries
  (``{"type": "function", "function": {...}}``).
- :func:`to_anthropic_tools` — Anthropic Messages API ``tools`` entries
  (``{"name", "description", "input_schema"}``).
- :func:`to_gemini_function_declarations` — Google Gemini
  ``functionDeclarations`` entries.

Provider APIs constrain tool names (OpenAI: ``^[a-zA-Z0-9_-]{1,64}$``), and
canonical ``tool_id`` values contain characters outside that set, so every
exporter returns an :class:`ExportedTools` bundle carrying both the payload
and a deterministic ``name → tool_id`` mapping.  Resolve a provider tool-call
back to the catalog with :meth:`ExportedTools.resolve` before executing —
this pairs with ``routing/selection.py``'s "constrain before / validate
after" contract (issues #515/#479).

Sister exporters: :func:`~contextweaver.routing.selection.selection_schema`
emits a *selection* schema (pick an id); this module emits *tool definitions*
(call with arguments).  Both are pure and deterministic.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from contextweaver.exceptions import ConfigError, ItemNotFoundError

if TYPE_CHECKING:
    from contextweaver.routing.catalog import Catalog
    from contextweaver.types import SelectableItem

#: Providers the exporters support.
EXPORT_PROVIDERS: tuple[str, ...] = ("openai", "anthropic", "gemini")

#: Characters permitted in provider-facing tool names (OpenAI grammar — the
#: strictest of the three; using it everywhere keeps names portable).
_NAME_ALLOWED = re.compile(r"[^a-zA-Z0-9_-]")

#: Provider tool-name length cap (OpenAI's 64 is the strictest).
_NAME_MAX_LEN = 64

#: Fallback JSON Schema for tools with no argument schema: an object that
#: accepts no arguments.  Providers reject absent/empty schemas.
_EMPTY_SCHEMA: dict[str, Any] = {"type": "object", "properties": {}}


@dataclass
class ExportedTools:
    """Provider-shaped tool definitions plus the name→id resolution map.

    Attributes:
        provider: The provider the payload targets.
        tools: Wire-ready list — pass directly as the provider ``tools``
            (or ``functionDeclarations``) request field.
        name_to_tool_id: Deterministic mapping from the sanitised provider
            tool name back to the canonical catalog ``tool_id``.
    """

    provider: str
    tools: list[dict[str, Any]] = field(default_factory=list)
    name_to_tool_id: dict[str, str] = field(default_factory=dict)

    def resolve(self, provider_name: str) -> str:
        """Return the canonical ``tool_id`` for a provider tool-call name.

        Args:
            provider_name: The ``name`` the provider echoed in its tool call.

        Returns:
            The canonical catalog ``tool_id``.

        Raises:
            ItemNotFoundError: If *provider_name* is not one of the
                exported names (never guesses).
        """
        try:
            return self.name_to_tool_id[provider_name]
        except KeyError:
            known = ", ".join(sorted(self.name_to_tool_id))
            raise ItemNotFoundError(
                f"provider tool name {provider_name!r} is not in the exported "
                f"shortlist (known: {known})",
                hint="resolve() only accepts names produced by this export",
            ) from None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "provider": self.provider,
            "tools": [dict(tool) for tool in self.tools],
            "name_to_tool_id": dict(self.name_to_tool_id),
        }


def provider_tool_name(item: SelectableItem, taken: set[str]) -> str:
    """Return a deterministic provider-safe name for *item*.

    Uses the bare ``name`` when it survives sanitisation and is free;
    otherwise prefixes the namespace, then appends a numeric suffix — so
    same-named tools from different namespaces stay distinguishable.

    Args:
        item: The catalog item to name.
        taken: Names already allocated in this export (mutated).

    Returns:
        A unique name matching ``^[a-zA-Z0-9_-]{1,64}$``.
    """
    base = _NAME_ALLOWED.sub("_", item.name).strip("_") or "tool"
    candidate = base[:_NAME_MAX_LEN]
    if candidate in taken and item.namespace:
        prefixed = _NAME_ALLOWED.sub("_", f"{item.namespace}__{base}")
        candidate = prefixed.strip("_")[:_NAME_MAX_LEN]
    suffix = 2
    unique = candidate
    while unique in taken:
        tail = f"_{suffix}"
        unique = candidate[: _NAME_MAX_LEN - len(tail)] + tail
        suffix += 1
    taken.add(unique)
    return unique


def _schema_for(item: SelectableItem, catalog: Catalog | None) -> dict[str, Any]:
    """Return the full argument schema for *item*, hydrating when empty."""
    if item.args_schema:
        return dict(item.args_schema)
    if catalog is not None:
        hydrated = catalog.hydrate(item.id)
        if hydrated.args_schema:
            return dict(hydrated.args_schema)
    return dict(_EMPTY_SCHEMA)


def export_tools(
    items: list[SelectableItem],
    *,
    provider: str,
    catalog: Catalog | None = None,
) -> ExportedTools:
    """Export *items* in a provider's native tool wire format.

    Args:
        items: Routed shortlist items, in ranked order (preserved).
        provider: One of :data:`EXPORT_PROVIDERS`.
        catalog: Optional catalog used to hydrate items whose inline
            ``args_schema`` is empty (mirrors ``routing/hydration.py``'s
            "inline wins; sidecar fills empties" rule).

    Returns:
        An :class:`ExportedTools` bundle.

    Raises:
        ConfigError: If *provider* is not a recognised value.
    """
    if provider not in EXPORT_PROVIDERS:
        raise ConfigError(
            f"Unknown export provider {provider!r}; expected one of {EXPORT_PROVIDERS}"
        )
    exported = ExportedTools(provider=provider)
    taken: set[str] = set()
    for item in items:
        name = provider_tool_name(item, taken)
        schema = _schema_for(item, catalog)
        description = item.description or item.name
        if provider == "openai":
            tool: dict[str, Any] = {
                "type": "function",
                "function": {
                    "name": name,
                    "description": description,
                    "parameters": schema,
                },
            }
        elif provider == "anthropic":
            tool = {"name": name, "description": description, "input_schema": schema}
        else:  # gemini
            tool = {"name": name, "description": description, "parameters": schema}
        exported.tools.append(tool)
        exported.name_to_tool_id[name] = item.id
    return exported


def to_openai_tools(
    items: list[SelectableItem], *, catalog: Catalog | None = None
) -> ExportedTools:
    """Export *items* as OpenAI Chat Completions ``tools`` entries."""
    return export_tools(items, provider="openai", catalog=catalog)


def to_anthropic_tools(
    items: list[SelectableItem], *, catalog: Catalog | None = None
) -> ExportedTools:
    """Export *items* as Anthropic Messages API ``tools`` entries."""
    return export_tools(items, provider="anthropic", catalog=catalog)


def to_gemini_function_declarations(
    items: list[SelectableItem], *, catalog: Catalog | None = None
) -> ExportedTools:
    """Export *items* as Google Gemini ``functionDeclarations`` entries.

    Pass ``exported.tools`` as the ``functionDeclarations`` list inside a
    Gemini ``tools`` request entry.
    """
    return export_tools(items, provider="gemini", catalog=catalog)


__all__ = [
    "EXPORT_PROVIDERS",
    "ExportedTools",
    "export_tools",
    "provider_tool_name",
    "to_anthropic_tools",
    "to_gemini_function_declarations",
    "to_openai_tools",
]

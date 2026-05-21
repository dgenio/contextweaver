"""Agno tool adapter for contextweaver (issue #275, child of #193).

Bridges [Agno](https://github.com/agno-agi/agno) (formerly Phidata)
tools into contextweaver's :class:`~contextweaver.types.SelectableItem`
catalog so an Agno ``Agent`` can route through contextweaver's
bounded-choice router instead of dumping every ``Function`` / ``Toolkit``
entry into the prompt.

Public surface:

- :func:`agno_tool_to_selectable` — single tool dict → ``SelectableItem``.
- :func:`agno_tools_to_catalog` — list of tool dicts → ``Catalog``.
- :func:`load_agno_catalog` — live ``Function`` / ``Toolkit`` instances → ``Catalog``.
- :func:`infer_agno_namespace` — namespace inference shared with the
  CrewAI / Pydantic AI / smolagents / FastMCP adapters.

The Agno ``Agent.memory.messages`` / ``RunResponse.messages`` ingestion
lives in :mod:`.agno_messages` to keep this module within the repo's
≤300-line guideline.

The plain-dict conversion path works without ``agno`` installed — the
dicts must mirror the shape exposed on ``agno.tools.function.Function``
or the result of ``Toolkit.functions[name].to_dict()``.  Live conversion
of real Agno tools requires the ``contextweaver[agno]`` optional extra.

Agno tools docs: https://docs.agno.com/concepts/tools/
"""

from __future__ import annotations

import copy
import logging
from typing import Any

from contextweaver.exceptions import CatalogError
from contextweaver.routing.catalog import Catalog
from contextweaver.types import SelectableItem

logger = logging.getLogger("contextweaver.adapters")

_FALLBACK_NS = "agno"
_ID_PREFIX = "agno:"


def infer_agno_namespace(tool_name: str) -> str:
    """Infer a namespace from an Agno tool name.

    Agno's ``Toolkit`` family ships tools whose names follow the
    convention ``<toolkit>_<verb>`` (``duckduckgo_search``,
    ``wikipedia_search``, ``yfinance_get_company_info``).  This helper
    extracts the leading prefix; dotted and slashed names are accepted
    too.  Falls back to ``"agno"`` for single-segment names.

    Args:
        tool_name: The raw tool name string.

    Returns:
        The inferred namespace.
    """
    if not tool_name:
        return _FALLBACK_NS
    if "." in tool_name:
        prefix = tool_name.split(".", 1)[0]
        if prefix:
            return prefix
    if "/" in tool_name:
        prefix = tool_name.split("/", 1)[0]
        if prefix:
            return prefix
    parts = tool_name.split("_")
    if len(parts) >= 2 and parts[0] and not parts[0].startswith("_"):
        return parts[0]
    return _FALLBACK_NS


def _strip_namespace_prefix(tool_name: str, namespace: str) -> str:
    for prefix in (f"{namespace}_", f"{namespace}.", f"{namespace}/"):
        if tool_name.startswith(prefix) and len(tool_name) > len(prefix):
            return tool_name[len(prefix) :]
    return tool_name


def _args_schema_dict(raw: object) -> dict[str, Any]:
    """Coerce an Agno ``parameters`` value into a JSON-shaped dict.

    Agno's ``Function.parameters`` carries the JSON Schema directly.
    Tools authored via the ``@tool`` decorator sometimes hand an
    ``inspect.Signature`` instead; we treat anything non-dict as
    schema-less rather than guessing.
    """
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return copy.deepcopy(raw)
    schema_fn = getattr(raw, "model_json_schema", None)
    if callable(schema_fn):
        try:
            schema = schema_fn()
        except Exception:  # pragma: no cover - defensive; depends on user model
            return {}
        if isinstance(schema, dict):
            return dict(schema)
    return {}


def agno_tool_to_selectable(
    tool_def: dict[str, Any],
    *,
    namespace: str | None = None,
) -> SelectableItem:
    """Convert an Agno tool definition dict to a :class:`SelectableItem`.

    The dict shape mirrors the field set on ``agno.tools.function.Function``
    (and the entries you get back from ``Toolkit.functions``):

    - ``name`` (required): the tool's display name.
    - ``description`` (required): natural-language description.
    - ``parameters`` (optional): JSON Schema dict for the tool's args.
    - ``tags`` (optional): list of tag strings.
    - ``strict`` (optional): Agno's strict-arg-validation flag,
      surfaced as ``metadata["strict"]``.
    - ``show_result`` (optional): Agno flag indicating the tool's raw
      output should be shown verbatim to the user, surfaced as
      ``metadata["show_result"]``.
    - ``stop_after_tool_call`` (optional): Agno flag indicating the
      agent loop should terminate after this tool, surfaced as
      ``metadata["stop_after_tool_call"]``.

    Args:
        tool_def: Raw tool definition dict.
        namespace: Explicit namespace override.  When ``None``, the
            namespace is inferred from the tool name.

    Returns:
        A :class:`SelectableItem` with ``kind="tool"`` and ``id`` of the
        form ``"agno:{name}"``.

    Raises:
        CatalogError: If ``name`` or ``description`` are missing or empty.
    """
    raw_name = tool_def.get("name")
    if not isinstance(raw_name, str) or not raw_name:
        raise CatalogError("Agno tool definition is missing a non-empty 'name' field.")
    raw_description = tool_def.get("description")
    if not isinstance(raw_description, str) or not raw_description:
        raise CatalogError(f"Agno tool {raw_name!r} is missing a non-empty 'description' field.")

    full_name = raw_name
    ns = namespace if namespace is not None else infer_agno_namespace(full_name)
    short_name = _strip_namespace_prefix(full_name, ns)

    raw_tags = tool_def.get("tags")
    tags: set[str] = {_FALLBACK_NS}
    if isinstance(raw_tags, (list, set, tuple)):
        for tag in raw_tags:
            if isinstance(tag, str) and tag:
                tags.add(tag)

    args_schema = _args_schema_dict(tool_def.get("parameters"))

    metadata: dict[str, Any] = {}
    for flag in ("strict", "show_result", "stop_after_tool_call"):
        if flag in tool_def and tool_def[flag] is not None:
            metadata[flag] = bool(tool_def[flag])

    toolkit_name = tool_def.get("toolkit")
    if isinstance(toolkit_name, str) and toolkit_name:
        metadata["toolkit"] = toolkit_name

    logger.debug(
        "agno_tool_to_selectable: name=%s, ns=%s, tags=%s",
        full_name,
        ns,
        sorted(tags),
    )
    return SelectableItem(
        id=f"{_ID_PREFIX}{full_name}",
        kind="tool",
        name=short_name,
        description=raw_description,
        tags=sorted(tags),
        namespace=ns,
        args_schema=args_schema,
        metadata=metadata,
    )


def agno_tools_to_catalog(
    tools: list[dict[str, Any]],
    *,
    namespace: str | None = None,
) -> Catalog:
    """Convert Agno tool definitions to a populated :class:`Catalog`.

    Args:
        tools: List of raw tool definition dicts.
        namespace: Optional uniform namespace override.

    Returns:
        A :class:`Catalog` with all converted items registered.

    Raises:
        CatalogError: If a tool definition is invalid or duplicate IDs are
            encountered.
    """
    catalog = Catalog()
    for tool_def in tools:
        item = agno_tool_to_selectable(tool_def, namespace=namespace)
        catalog.register(item)
    logger.debug("agno_tools_to_catalog: registered %d items", len(tools))
    return catalog


def _dump_agno_tool(tool: object, toolkit_name: str | None = None) -> dict[str, Any]:
    """Convert a live Agno ``Function`` / decorated callable to the adapter's dict shape.

    Accepts:

    - ``agno.tools.function.Function`` instances (the canonical wrapper
      Agno builds around plain callables and ``Toolkit`` methods).
    - Any object exposing ``name`` + ``description`` attributes.
    - Plain dicts (returned as-is after validation).
    """
    if isinstance(tool, dict):
        dict_out: dict[str, Any] = dict(tool)
        if toolkit_name and "toolkit" not in dict_out:
            dict_out["toolkit"] = toolkit_name
        return dict_out

    name = getattr(tool, "name", None)
    description = getattr(tool, "description", None)
    if not isinstance(name, str) or not name:
        raise CatalogError(f"Agno tool {tool!r} is missing a non-empty 'name' attribute.")
    if not isinstance(description, str) or not description:
        raise CatalogError(f"Agno tool {name!r} is missing a non-empty 'description' attribute.")

    parameters = getattr(tool, "parameters", None)
    out: dict[str, Any] = {
        "name": name,
        "description": description,
        "parameters": parameters if isinstance(parameters, dict) else None,
        "tags": list(getattr(tool, "tags", []) or []),
        "strict": getattr(tool, "strict", None),
        "show_result": getattr(tool, "show_result", None),
        "stop_after_tool_call": getattr(tool, "stop_after_tool_call", None),
    }
    if toolkit_name:
        out["toolkit"] = toolkit_name
    return out


def load_agno_catalog(
    tools: list[object],
    *,
    namespace: str | None = None,
) -> Catalog:
    """Convert live Agno ``Function`` / ``Toolkit`` instances to a :class:`Catalog`.

    Two entry shapes are recognised:

    - A live ``Toolkit`` (anything exposing a ``functions`` dict or list
      attribute *and* a ``name`` attribute).  Every entry becomes a
      ``SelectableItem`` and inherits the toolkit's name as
      ``metadata["toolkit"]``.
    - A live ``Function`` or any duck-typed object with
      ``name`` + ``description``.

    Args:
        tools: Live Agno tool entries.
        namespace: Optional uniform namespace override.

    Returns:
        A populated :class:`Catalog`.

    Raises:
        CatalogError: If a tool entry is missing required attributes.
    """
    tool_dicts: list[dict[str, Any]] = []
    for entry in tools:
        toolkit_name = getattr(entry, "name", None) if hasattr(entry, "functions") else None
        functions = getattr(entry, "functions", None)
        if isinstance(functions, dict):
            for fn in functions.values():
                tool_dicts.append(_dump_agno_tool(fn, toolkit_name))
        elif isinstance(functions, (list, tuple)):
            for fn in functions:
                tool_dicts.append(_dump_agno_tool(fn, toolkit_name))
        else:
            tool_dicts.append(_dump_agno_tool(entry))
    return agno_tools_to_catalog(tool_dicts, namespace=namespace)

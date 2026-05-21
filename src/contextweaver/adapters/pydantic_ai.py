"""Pydantic AI tool adapter for contextweaver (issue #272, child of #193).

Bridges [Pydantic AI](https://ai.pydantic.dev/) tools into contextweaver's
:class:`~contextweaver.types.SelectableItem` catalog so a Pydantic AI
``Agent`` can route through contextweaver's bounded-choice router
instead of dumping every typed tool definition into the prompt.

Public surface:

- :func:`pydantic_ai_tool_to_selectable` — single tool dict → ``SelectableItem``.
- :func:`pydantic_ai_tools_to_catalog` — list of tool dicts → populated ``Catalog``.
- :func:`load_pydantic_ai_catalog` — list of live ``Tool`` / ``FunctionToolset``
  instances → populated ``Catalog``.
- :func:`infer_pydantic_ai_namespace` — namespace inference rule shared
  with the CrewAI / FastMCP adapters.

The message-history round-trip lives in :mod:`.pydantic_ai_messages` to
keep this module within the repo's ≤300-line guideline.

The plain-dict conversion path works without ``pydantic_ai`` installed —
the dicts must mirror the shape returned by ``Tool.model_dump()``.  Live
conversion of real ``pydantic_ai.tools.Tool`` instances requires the
``contextweaver[pydantic-ai]`` optional extra.

Pydantic AI tool docs: https://ai.pydantic.dev/tools/
"""

from __future__ import annotations

import copy
import logging
from typing import Any

from contextweaver.exceptions import CatalogError
from contextweaver.routing.catalog import Catalog
from contextweaver.types import SelectableItem

logger = logging.getLogger("contextweaver.adapters")

_FALLBACK_NS = "pydantic_ai"
_ID_PREFIX = "pydantic_ai:"


def infer_pydantic_ai_namespace(tool_name: str) -> str:
    """Infer a namespace from a Pydantic AI tool name.

    Pydantic AI tools are usually plain Python identifiers; the convention
    is to either use snake_case (``search_repos``) or dotted prefixes
    (``github.search_repos``).  This helper applies the same separator
    rules the CrewAI / FastMCP adapters use, falling back to
    ``"pydantic_ai"`` when no prefix is present.

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
    """Coerce a Pydantic AI tool parameters object into a JSON-shaped dict.

    Pydantic AI tools expose their argument schema via a Pydantic model on
    the ``Tool`` instance; ``model_json_schema()`` (when present) or the
    model's own ``model_json_schema()`` returns the JSON Schema dict
    contextweaver consumes.  Plain dicts are accepted as-is.
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


def pydantic_ai_tool_to_selectable(
    tool_def: dict[str, Any],
    *,
    namespace: str | None = None,
) -> SelectableItem:
    """Convert a Pydantic AI tool definition dict to a :class:`SelectableItem`.

    The dict shape mirrors the field set on ``pydantic_ai.tools.Tool``:

    - ``name`` (required): the tool's display name.
    - ``description`` (required): natural-language description.
    - ``parameters_json_schema`` or ``args_schema`` (optional): JSON Schema
      dict or a Pydantic model class with ``model_json_schema()``.
    - ``tags`` (optional): list of tag strings.
    - ``strict`` (optional): Pydantic AI's strict-arg-validation flag,
      surfaced as ``metadata["strict"]``.

    Args:
        tool_def: Raw tool definition dict.
        namespace: Explicit namespace override.  When ``None``, the
            namespace is inferred from the tool name.

    Returns:
        A :class:`SelectableItem` with ``kind="tool"`` and ``id`` of the
        form ``"pydantic_ai:{name}"``.

    Raises:
        CatalogError: If ``name`` or ``description`` are missing or empty.
    """
    raw_name = tool_def.get("name")
    if not isinstance(raw_name, str) or not raw_name:
        raise CatalogError("Pydantic AI tool definition is missing a non-empty 'name' field.")
    raw_description = tool_def.get("description")
    if not isinstance(raw_description, str) or not raw_description:
        raise CatalogError(
            f"Pydantic AI tool {raw_name!r} is missing a non-empty 'description' field."
        )

    full_name = raw_name
    ns = namespace if namespace is not None else infer_pydantic_ai_namespace(full_name)
    short_name = _strip_namespace_prefix(full_name, ns)

    raw_tags = tool_def.get("tags")
    tags: set[str] = {_FALLBACK_NS}
    if isinstance(raw_tags, (list, set, tuple)):
        for tag in raw_tags:
            if isinstance(tag, str) and tag:
                tags.add(tag)

    args_schema = _args_schema_dict(
        tool_def.get("parameters_json_schema") or tool_def.get("args_schema")
    )

    metadata: dict[str, Any] = {}
    if "strict" in tool_def and tool_def["strict"] is not None:
        metadata["strict"] = bool(tool_def["strict"])
    if "takes_ctx" in tool_def and tool_def["takes_ctx"] is not None:
        metadata["takes_ctx"] = bool(tool_def["takes_ctx"])

    logger.debug(
        "pydantic_ai_tool_to_selectable: name=%s, ns=%s, tags=%s",
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


def pydantic_ai_tools_to_catalog(
    tools: list[dict[str, Any]],
    *,
    namespace: str | None = None,
) -> Catalog:
    """Convert Pydantic AI tool definitions to a populated :class:`Catalog`.

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
        item = pydantic_ai_tool_to_selectable(tool_def, namespace=namespace)
        catalog.register(item)
    logger.debug("pydantic_ai_tools_to_catalog: registered %d items", len(tools))
    return catalog


def _dump_pydantic_ai_tool(tool: object) -> dict[str, Any]:
    """Convert a live Pydantic AI ``Tool`` instance into the adapter's dict shape.

    Tolerates partial-shape duck-typed objects: anything exposing
    ``name`` + ``description`` attributes is accepted.
    """
    name = getattr(tool, "name", None)
    description = getattr(tool, "description", None)
    if not isinstance(name, str) or not name:
        raise CatalogError(f"Pydantic AI tool {tool!r} is missing a non-empty 'name' attribute.")
    if not isinstance(description, str) or not description:
        raise CatalogError(
            f"Pydantic AI tool {name!r} is missing a non-empty 'description' attribute."
        )

    schema_source: object | None = None
    for attr in (
        "parameters_json_schema",
        "_parameters_json_schema",
        "args_schema",
        "schema",
    ):
        value = getattr(tool, attr, None)
        if value is not None:
            schema_source = value
            break
    if schema_source is None:
        json_schema_fn = getattr(tool, "json_schema", None) or getattr(
            tool, "function_schema", None
        )
        if callable(json_schema_fn):
            try:
                schema_source = json_schema_fn()
            except Exception:  # pragma: no cover - defensive
                schema_source = None

    return {
        "name": name,
        "description": description,
        "parameters_json_schema": schema_source,
        "tags": list(getattr(tool, "tags", []) or []),
        "strict": getattr(tool, "strict", None),
        "takes_ctx": getattr(tool, "takes_ctx", None),
    }


def load_pydantic_ai_catalog(
    tools: list[object],
    *,
    namespace: str | None = None,
) -> Catalog:
    """Convert live ``pydantic_ai.tools.Tool`` instances to a :class:`Catalog`.

    Accepts a ``FunctionToolset`` too — anything exposing an iterable
    ``tools`` attribute is flattened first.  Otherwise each element is
    treated as a live ``Tool`` (or any object with ``name`` /
    ``description``).

    Args:
        tools: Live ``Tool`` instances or a ``FunctionToolset``.
        namespace: Optional uniform namespace override.

    Returns:
        A populated :class:`Catalog`.

    Raises:
        CatalogError: If a tool object is missing required attributes.
    """
    flat: list[object] = []
    for entry in tools:
        nested = getattr(entry, "tools", None)
        if isinstance(nested, (list, tuple)):
            flat.extend(nested)
        elif isinstance(nested, dict):
            flat.extend(nested.values())
        else:
            flat.append(entry)
    return pydantic_ai_tools_to_catalog(
        [_dump_pydantic_ai_tool(t) for t in flat],
        namespace=namespace,
    )

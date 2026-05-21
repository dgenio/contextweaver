"""smolagents tool adapter for contextweaver (issue #274, child of #193).

Bridges [smolagents](https://github.com/huggingface/smolagents) tools
into contextweaver's :class:`~contextweaver.types.SelectableItem`
catalog so a HuggingFace ``MultiStepAgent`` can route through
contextweaver's bounded-choice router instead of dumping every
``Tool.description`` into the system prompt.

Public surface:

- :func:`smolagents_tool_to_selectable` — single tool dict → ``SelectableItem``.
- :func:`smolagents_tools_to_catalog` — list of tool dicts → ``Catalog``.
- :func:`load_smolagents_catalog` — live ``Tool`` instances → ``Catalog``.
- :func:`infer_smolagents_namespace` — namespace inference shared with
  the CrewAI / Pydantic AI / FastMCP adapters.

The per-step ``MultiStepAgent.memory`` ingestion lives in
:mod:`.smolagents_steps` to keep this module within the repo's
≤300-line guideline.

The plain-dict conversion path works without ``smolagents`` installed —
the dicts must mirror the shape exposed on
``smolagents.tools.Tool``: ``name``, ``description``, ``inputs``,
``output_type``.  Live conversion of real ``Tool`` instances requires
the ``contextweaver[smolagents]`` optional extra.

smolagents tool docs: https://huggingface.co/docs/smolagents/main/en/tutorials/tools
"""

from __future__ import annotations

import copy
import logging
from typing import Any

from contextweaver.exceptions import CatalogError
from contextweaver.routing.catalog import Catalog
from contextweaver.types import SelectableItem

logger = logging.getLogger("contextweaver.adapters")

_FALLBACK_NS = "smolagents"
_ID_PREFIX = "smolagents:"


def infer_smolagents_namespace(tool_name: str) -> str:
    """Infer a namespace from a smolagents tool name.

    smolagents tools are normally exposed under plain snake_case names
    (``web_search``, ``code_interpreter``).  When a tool author uses a
    dotted / slashed prefix (``hub.image_classification``) this helper
    extracts that prefix; otherwise it falls back to ``"smolagents"``.

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


def _inputs_to_args_schema(inputs: object) -> dict[str, Any]:
    """Convert a smolagents ``Tool.inputs`` mapping to a JSON-schema dict.

    smolagents represents arguments as a flat mapping
    ``{arg_name: {"type": "string", "description": "..."}}``.  Items
    flagged ``"nullable": True`` become optional; everything else is
    required.  This matches the JSON-Schema shape contextweaver's router
    consumes via ``SelectableItem.args_schema``.
    """
    if not isinstance(inputs, dict):
        return {}
    properties: dict[str, Any] = {}
    required: list[str] = []
    for arg_name, spec in inputs.items():
        if not isinstance(arg_name, str) or not arg_name:
            continue
        if not isinstance(spec, dict):
            continue
        properties[arg_name] = copy.deepcopy(spec)
        if not spec.get("nullable", False):
            required.append(arg_name)
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = required
    return schema


def smolagents_tool_to_selectable(
    tool_def: dict[str, Any],
    *,
    namespace: str | None = None,
) -> SelectableItem:
    """Convert a smolagents tool definition dict to a :class:`SelectableItem`.

    The dict shape mirrors the field set on ``smolagents.tools.Tool``:

    - ``name`` (required): the tool's display name.
    - ``description`` (required): natural-language description used by
      the LLM at routing time.
    - ``inputs`` (optional): ``Tool.inputs`` mapping
      (``{arg_name: {"type": ..., "description": ..., "nullable": ...}}``).
      Converted to a JSON-Schema ``{"type": "object", "properties": ...,
      "required": [...]}`` dict.
    - ``output_type`` (optional): smolagents return-type tag
      (``"string"`` / ``"image"`` / ``"audio"`` / ``"any"`` etc.),
      surfaced as ``metadata["output_type"]``.
    - ``tags`` (optional): list of tag strings.
    - ``skip_forward_signature_validation`` (optional): smolagents flag,
      surfaced as ``metadata["skip_forward_signature_validation"]``.

    Args:
        tool_def: Raw tool definition dict.
        namespace: Explicit namespace override.  When ``None``, the
            namespace is inferred from the tool name.

    Returns:
        A :class:`SelectableItem` with ``kind="tool"`` and ``id`` of the
        form ``"smolagents:{name}"``.

    Raises:
        CatalogError: If ``name`` or ``description`` are missing or empty.
    """
    raw_name = tool_def.get("name")
    if not isinstance(raw_name, str) or not raw_name:
        raise CatalogError("smolagents tool definition is missing a non-empty 'name' field.")
    raw_description = tool_def.get("description")
    if not isinstance(raw_description, str) or not raw_description:
        raise CatalogError(
            f"smolagents tool {raw_name!r} is missing a non-empty 'description' field."
        )

    full_name = raw_name
    ns = namespace if namespace is not None else infer_smolagents_namespace(full_name)
    short_name = _strip_namespace_prefix(full_name, ns)

    raw_tags = tool_def.get("tags")
    tags: set[str] = {_FALLBACK_NS}
    if isinstance(raw_tags, (list, set, tuple)):
        for tag in raw_tags:
            if isinstance(tag, str) and tag:
                tags.add(tag)

    args_schema = _inputs_to_args_schema(tool_def.get("inputs"))

    metadata: dict[str, Any] = {}
    output_type = tool_def.get("output_type")
    if isinstance(output_type, str) and output_type:
        metadata["output_type"] = output_type
    if "skip_forward_signature_validation" in tool_def:
        metadata["skip_forward_signature_validation"] = bool(
            tool_def["skip_forward_signature_validation"]
        )

    logger.debug(
        "smolagents_tool_to_selectable: name=%s, ns=%s, tags=%s",
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


def smolagents_tools_to_catalog(
    tools: list[dict[str, Any]],
    *,
    namespace: str | None = None,
) -> Catalog:
    """Convert smolagents tool definitions to a populated :class:`Catalog`.

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
        item = smolagents_tool_to_selectable(tool_def, namespace=namespace)
        catalog.register(item)
    logger.debug("smolagents_tools_to_catalog: registered %d items", len(tools))
    return catalog


def _dump_smolagents_tool(tool: object) -> dict[str, Any]:
    """Convert a live ``smolagents.tools.Tool`` instance to the adapter's dict shape.

    Accepts any duck-typed object exposing ``name``, ``description``, and
    optionally ``inputs`` / ``output_type`` attributes.
    """
    name = getattr(tool, "name", None)
    description = getattr(tool, "description", None)
    if not isinstance(name, str) or not name:
        raise CatalogError(f"smolagents tool {tool!r} is missing a non-empty 'name' attribute.")
    if not isinstance(description, str) or not description:
        raise CatalogError(
            f"smolagents tool {name!r} is missing a non-empty 'description' attribute."
        )
    inputs = getattr(tool, "inputs", None)
    output_type = getattr(tool, "output_type", None)
    return {
        "name": name,
        "description": description,
        "inputs": inputs if isinstance(inputs, dict) else {},
        "output_type": output_type if isinstance(output_type, str) else None,
        "tags": list(getattr(tool, "tags", []) or []),
    }


def load_smolagents_catalog(
    tools: list[object],
    *,
    namespace: str | None = None,
) -> Catalog:
    """Convert live ``smolagents.tools.Tool`` instances to a :class:`Catalog`.

    Accepts a smolagents ``Toolbox`` too: anything exposing an iterable
    ``tools`` attribute (list / tuple / dict) is flattened first.

    Args:
        tools: Live ``Tool`` instances, a ``Toolbox``, or any
            duck-typed objects exposing ``name`` / ``description``
            (plus optional ``inputs`` / ``output_type``).
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
    return smolagents_tools_to_catalog(
        [_dump_smolagents_tool(t) for t in flat],
        namespace=namespace,
    )

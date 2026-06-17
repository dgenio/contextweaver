"""Shared conversion scaffolding for framework tool-catalog adapters (issue #454).

The framework adapters (:mod:`.crewai`, :mod:`.agno`, :mod:`.smolagents`,
:mod:`.pydantic_ai`, :mod:`.chainweaver`, and the newer :mod:`.langchain` /
:mod:`.openai_agents` / :mod:`.google_adk` adapters) each used to re-implement
the same conversion mechanics: namespace inference, namespace-prefix stripping,
defensive schema-dict coercion, tag collection, and required-field validation.

Centralising the genuinely shared parts here means a convention change (canonical
``tool_id`` handling, the reserved-metadata rule, a new schema-coercion edge
case) is a single edit rather than up to five, and a new adapter copies a small
documented checklist rather than the newest hand-rolled variant.

Per ``AGENTS.md`` adapter conventions this module is a **pure, stateless**
helper: it imports no framework library and performs no I/O.  It is private —
its API is not exported from :mod:`contextweaver.adapters` and is not part of
the public contract.  Each adapter keeps its own framework-specific mapping
(id prefix, metadata fields, message/session ingestion) local; only the
trivially-parameterisable mechanics live here.
"""

from __future__ import annotations

import copy
from typing import Any

from contextweaver.exceptions import CatalogError


def infer_namespace(tool_name: str, *, fallback: str) -> str:
    """Infer a namespace from a tool name using prefix heuristics.

    Tools are commonly named with a dot-, slash-, or underscore-separated
    prefix (``calendar.create_event``, ``filesystem/read_file``,
    ``github_search``).  The first such segment becomes the namespace; when no
    prefix can be detected the *fallback* is returned.

    Args:
        tool_name: The raw tool name string.
        fallback: The namespace returned when no prefix is detectable
            (each adapter passes its framework name, e.g. ``"crewai"``).

    Returns:
        The inferred namespace string.
    """
    if not tool_name:
        return fallback
    for sep in (".", "/"):
        if sep in tool_name:
            prefix = tool_name.split(sep, 1)[0]
            if prefix:
                return prefix
    parts = tool_name.split("_")
    if len(parts) >= 2 and parts[0] and not parts[0].startswith("_"):
        return parts[0]
    return fallback


def strip_namespace_prefix(tool_name: str, namespace: str) -> str:
    """Return the short tool name with the namespace prefix removed.

    A leading ``"{namespace}_"`` / ``"{namespace}."`` / ``"{namespace}/"``
    segment is stripped; the full name is returned unchanged when no such
    prefix is present (or stripping it would leave an empty name).

    Args:
        tool_name: The raw tool name string.
        namespace: The namespace whose prefix should be removed.

    Returns:
        The namespace-free short name.
    """
    for prefix in (f"{namespace}_", f"{namespace}.", f"{namespace}/"):
        if tool_name.startswith(prefix) and len(tool_name) > len(prefix):
            return tool_name[len(prefix) :]
    return tool_name


def coerce_schema_dict(raw: object) -> dict[str, Any]:
    """Coerce a framework schema value into a JSON-shaped dict.

    Accepts an already-built JSON-Schema ``dict`` (deep-copied so the caller's
    input is never mutated) or a Pydantic model class exposing
    ``model_json_schema``.  Anything else — including ``None`` and a model whose
    ``model_json_schema()`` raises — yields ``{}``.

    Args:
        raw: A schema dict, a Pydantic model class, or ``None``.

    Returns:
        A JSON-Schema dict, or ``{}`` when no schema could be derived.
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
            return copy.deepcopy(schema)
    return {}


def collect_tags(raw_tags: object, *, fallback: str) -> list[str]:
    """Merge user-provided tags with a mandatory *fallback* tag.

    The *fallback* tag is always present.  Only non-empty string entries from
    *raw_tags* (when it is a list / set / tuple) are merged in.  The result is
    sorted for determinism.

    Args:
        raw_tags: The raw ``tags`` value off a tool definition (any type).
        fallback: A tag always included (the framework name, or ``"flow"``).

    Returns:
        A sorted list of tag strings.
    """
    tags: set[str] = {fallback}
    if isinstance(raw_tags, (list, set, tuple)):
        for tag in raw_tags:
            if isinstance(tag, str) and tag:
                tags.add(tag)
    return sorted(tags)


def require_name_description(tool_def: dict[str, Any], *, label: str) -> tuple[str, str]:
    """Validate and return the required ``name`` / ``description`` fields.

    Args:
        tool_def: The raw tool definition dict.
        label: The framework display name used in error messages
            (e.g. ``"CrewAI"``, ``"Agno"``, ``"LangChain"``).

    Returns:
        A ``(name, description)`` tuple of validated, non-empty strings.

    Raises:
        CatalogError: If ``name`` or ``description`` is missing or non-string.
    """
    raw_name = tool_def.get("name")
    if not isinstance(raw_name, str) or not raw_name:
        raise CatalogError(f"{label} tool definition is missing a non-empty 'name' field.")
    raw_description = tool_def.get("description")
    if not isinstance(raw_description, str) or not raw_description:
        raise CatalogError(f"{label} tool {raw_name!r} is missing a non-empty 'description' field.")
    return raw_name, raw_description

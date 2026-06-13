"""OpenAPI → catalog adapter for contextweaver (issue #546).

Converts an [OpenAPI](https://spec.openapis.org/) document (3.0 or 3.1) into a
routing :class:`~contextweaver.routing.catalog.Catalog`: every operation
becomes a :class:`~contextweaver.types.SelectableItem` so teams whose "tools"
are REST APIs get bounded-choice routing without hand-building a catalog.  This
is the same context-rot problem the library exists for — a real-world spec with
hundreds of operations overflows a prompt immediately.

Mapping (one operation → one item):

- ``operationId`` (fallback: ``{method}_{path-slug}``) → ``id`` / ``name``.
- ``summary`` / ``description`` → ``description`` (falls back to
  ``"{METHOD} {path}"`` when both are absent).
- OpenAPI ``tags`` + method-derived safety tags (``GET`` / ``HEAD`` →
  ``read-only``, ``DELETE`` → ``destructive``, mirroring the MCP adapter) → tags.
- ``parameters`` + ``requestBody`` → a single ``args_schema`` object (see
  :func:`contextweaver.adapters._openapi_schema.compose_args_schema` for the
  composition rule).
- ``path``, ``method``, ``operationId``, ``deprecated``, top-level ``servers``
  and operation ``security`` → ``metadata`` (auth / server info is preserved
  for reference only).

Scope: contextweaver *routes* — it never executes HTTP calls; dispatching the
selected operation stays with the caller's HTTP client.  Only document-local
``$ref``s are resolved; external refs raise (no network fetching).  This module
is a pure adapter (PyYAML + ``jsonschema`` are core deps); it imports no HTTP
client.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any

from contextweaver.adapters._framework_common import collect_tags
from contextweaver.adapters._openapi_schema import (
    HTTP_METHODS,
    compose_args_schema,
    load_spec,
    operation_safety,
    resolve_refs,
)
from contextweaver.exceptions import CatalogError
from contextweaver.routing.catalog import Catalog
from contextweaver.types import SelectableItem

logger = logging.getLogger("contextweaver.adapters")

_FALLBACK_NS = "openapi"
_ID_PREFIX = "openapi"
_SLUG_RE = re.compile(r"[^a-zA-Z0-9]+")


def _slug(path: str) -> str:
    """Turn a URL path template into a stable identifier slug."""
    return _SLUG_RE.sub("_", path).strip("_").lower() or "root"


def infer_openapi_namespace(
    operation: dict[str, Any],
    path: str,
    *,
    base_namespace: str | None = None,
) -> str:
    """Infer a namespace for an operation.

    Resolution order: an explicit *base_namespace*, then the operation's first
    OpenAPI ``tag``, then the first non-templated path segment, then
    ``"openapi"``.

    Args:
        operation: The operation object.
        path: The URL path template (e.g. ``"/pets/{id}"``).
        base_namespace: Optional explicit namespace.

    Returns:
        The inferred namespace string.
    """
    if base_namespace:
        return base_namespace
    tags = operation.get("tags")
    if isinstance(tags, list):
        for tag in tags:
            if isinstance(tag, str) and tag:
                return tag
    for segment in path.split("/"):
        if segment and not segment.startswith("{"):
            return segment
    return _FALLBACK_NS


def _operation_identity(operation: dict[str, Any], method: str, path: str) -> str:
    """Return the operationId, or a deterministic ``{method}_{slug}`` fallback."""
    operation_id = operation.get("operationId")
    if isinstance(operation_id, str) and operation_id:
        return operation_id
    return f"{method.lower()}_{_slug(path)}"


def _operation_description(operation: dict[str, Any], method: str, path: str) -> str:
    """Compose a non-empty routable description from summary / description."""
    summary = operation.get("summary")
    description = operation.get("description")
    parts = [p for p in (summary, description) if isinstance(p, str) and p.strip()]
    if parts:
        return "\n\n".join(parts)
    return f"{method.upper()} {path}"


def openapi_operation_to_selectable(
    operation: dict[str, Any],
    *,
    path: str,
    method: str,
    root: dict[str, Any] | None = None,
    base_namespace: str | None = None,
    shared_parameters: list[Any] | None = None,
    shared_servers: list[Any] | None = None,
) -> SelectableItem:
    """Convert a single OpenAPI operation to a :class:`SelectableItem`.

    Args:
        operation: The operation object (``get`` / ``post`` / ... value of a
            path item).
        path: The URL path template the operation lives under.
        method: The HTTP method (``"get"``, ``"post"``, ...).
        root: The full spec document, used to resolve local ``$ref``s.  When
            ``None``, *operation* is treated as already self-contained.
        base_namespace: Optional explicit namespace override.
        shared_parameters: Path-item-level parameters that apply to the
            operation (merged ahead of operation-level parameters).
        shared_servers: Path-item-level ``servers`` that apply to the
            operation. Precedence for the ``metadata["servers"]`` value is
            operation-level ``servers`` → these path-level ``servers`` →
            document-level ``servers``.

    Returns:
        A :class:`SelectableItem` with ``kind="tool"`` and an ``id`` of
        ``"openapi:{operationId}"``.

    Raises:
        CatalogError: If a local ``$ref`` cannot be resolved or an external
            ``$ref`` is encountered.
    """
    base = root if root is not None else operation
    identity = _operation_identity(operation, method, path)
    description = _operation_description(operation, method, path)

    safety_tags, side_effects = operation_safety(method)
    operation_tags = operation.get("tags")
    raw_tags = (operation_tags if isinstance(operation_tags, list) else []) + safety_tags
    tags = collect_tags(raw_tags, fallback=_FALLBACK_NS)

    args_schema = compose_args_schema(operation, base, shared_parameters=shared_parameters)
    ns = infer_openapi_namespace(operation, path, base_namespace=base_namespace)

    metadata: dict[str, Any] = {
        "runtime": _FALLBACK_NS,
        "http_method": method.upper(),
        "http_path": path,
        "operation_id": identity,
    }
    if operation.get("deprecated"):
        metadata["deprecated"] = True
    security = operation.get("security")
    if security is not None:
        metadata["security"] = resolve_refs(security, base)
    # servers precedence: operation-level → path-item (shared_servers) → document.
    op_servers = operation.get("servers")
    root_servers = root.get("servers") if root is not None else None
    if isinstance(op_servers, list):
        servers: Any = op_servers
    elif isinstance(shared_servers, list):
        servers = shared_servers
    elif isinstance(root_servers, list):
        servers = root_servers
    else:
        servers = None
    if servers is not None:
        metadata["servers"] = resolve_refs(servers, base)

    logger.debug(
        "openapi_operation_to_selectable: id=%s, method=%s, path=%s, ns=%s",
        identity,
        method,
        path,
        ns,
    )
    return SelectableItem(
        id=f"{_ID_PREFIX}:{identity}",
        kind="tool",
        name=identity,
        description=description,
        tags=tags,
        namespace=ns,
        args_schema=args_schema,
        side_effects=side_effects,
        metadata=metadata,
    )


def openapi_spec_to_catalog(
    spec: dict[str, Any],
    *,
    base_namespace: str | None = None,
) -> Catalog:
    """Convert a parsed OpenAPI document into a populated :class:`Catalog`.

    Iterates every path item and every HTTP-method operation on it, in sorted
    ``(path, method)`` order for deterministic catalog construction.

    Args:
        spec: A parsed OpenAPI document dict.
        base_namespace: Optional namespace override applied to every operation.

    Returns:
        A populated :class:`~contextweaver.routing.catalog.Catalog`.

    Raises:
        CatalogError: If ``paths`` is missing / malformed, a ``$ref`` cannot be
            resolved, or two operations resolve to the same id (duplicate
            ``operationId`` or colliding fallback slugs).
    """
    paths = spec.get("paths")
    if not isinstance(paths, dict):
        raise CatalogError("OpenAPI spec must carry a 'paths' mapping.")

    catalog = Catalog()
    count = 0
    for path in sorted(paths):
        path_item = paths[path]
        if not isinstance(path_item, dict):
            continue
        shared = path_item.get("parameters")
        shared_params = shared if isinstance(shared, list) else None
        path_servers = path_item.get("servers")
        shared_servers = path_servers if isinstance(path_servers, list) else None
        for method in HTTP_METHODS:
            operation = path_item.get(method)
            if not isinstance(operation, dict):
                continue
            catalog.register(
                openapi_operation_to_selectable(
                    operation,
                    path=path,
                    method=method,
                    root=spec,
                    base_namespace=base_namespace,
                    shared_parameters=shared_params,
                    shared_servers=shared_servers,
                )
            )
            count += 1
    logger.debug("openapi_spec_to_catalog: registered %d operations", count)
    return catalog


def load_openapi_catalog(
    spec_or_path: dict[str, Any] | str | Path,
    *,
    base_namespace: str | None = None,
) -> Catalog:
    """Load an OpenAPI document (dict / JSON path / YAML path) into a :class:`Catalog`.

    Convenience entry point combining
    :func:`contextweaver.adapters._openapi_schema.load_spec` and
    :func:`openapi_spec_to_catalog`.

    Args:
        spec_or_path: A parsed spec dict, or a filesystem path to a JSON / YAML
            OpenAPI document.
        base_namespace: Optional namespace override applied to every operation.

    Returns:
        A populated :class:`Catalog`.

    Raises:
        CatalogError: If the spec cannot be read / parsed or contains invalid
            operations.
    """
    return openapi_spec_to_catalog(load_spec(spec_or_path), base_namespace=base_namespace)


__all__ = [
    "infer_openapi_namespace",
    "load_openapi_catalog",
    "openapi_operation_to_selectable",
    "openapi_spec_to_catalog",
]

"""Schema mechanics for the OpenAPI adapter (issue #546).

Private module backing :mod:`contextweaver.adapters.openapi`; kept separate so
``openapi.py`` stays within the ≤300-line module ceiling.  Not public API.

Holds the genuinely fiddly OpenAPI bits: spec loading (JSON / YAML / dict),
local ``$ref`` resolution (external refs are rejected for the security
posture), the ``parameters`` + ``requestBody`` → single ``args_schema``
composition rule, and the HTTP-method → safety-tag mapping (mirroring the MCP
adapter's ``readOnlyHint`` / ``destructiveHint`` conventions).

Pure / stateless: no I/O beyond reading a spec file at the ``load_*`` boundary,
no third-party imports other than the core PyYAML dependency.
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import yaml

from contextweaver.exceptions import CatalogError

#: OpenAPI Path Item fields that are HTTP operations (OpenAPI 3.0/3.1).
HTTP_METHODS: tuple[str, ...] = (
    "get",
    "put",
    "post",
    "delete",
    "options",
    "head",
    "patch",
    "trace",
)
#: Methods with no server-state side effects (safe / idempotent reads).
_READ_METHODS = frozenset({"get", "head", "options", "trace"})


def load_spec(spec_or_path: dict[str, Any] | str | Path) -> dict[str, Any]:
    """Load an OpenAPI document from a dict, or a JSON / YAML file path.

    Args:
        spec_or_path: An already-parsed spec dict, or a filesystem path to a
            ``.json`` / ``.yaml`` / ``.yml`` OpenAPI document.

    Returns:
        The parsed spec as a dict (the caller's dict is deep-copied so later
        mutations to the input never leak into the catalog).

    Raises:
        CatalogError: If the file cannot be read or parsed, or the top-level
            document is not a mapping.
    """
    if isinstance(spec_or_path, dict):
        return copy.deepcopy(spec_or_path)
    path = Path(spec_or_path)
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CatalogError(f"Cannot read OpenAPI spec file: {exc}") from exc
    try:
        # ``yaml.safe_load`` parses JSON too (JSON is a YAML subset), so a single
        # path handles both ``.json`` and ``.yaml`` inputs.
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise CatalogError(f"Invalid OpenAPI spec in {path!s}: {exc}") from exc
    if not isinstance(data, dict):
        raise CatalogError(
            f"OpenAPI spec {path!s} must be a mapping at the top level (got {type(data).__name__})."
        )
    return data


def _resolve_pointer(ref: str, root: dict[str, Any]) -> Any:  # noqa: ANN401 — arbitrary JSON
    """Resolve a local JSON-pointer ``#/a/b/c`` against *root*."""
    parts = ref.lstrip("#/").split("/")
    node: Any = root
    for part in parts:
        # JSON-pointer unescaping (RFC 6901).
        key = part.replace("~1", "/").replace("~0", "~")
        if not isinstance(node, dict) or key not in node:
            raise CatalogError(f"OpenAPI $ref {ref!r} does not resolve within the document.")
        node = node[key]
    return node


def resolve_refs(
    node: Any,  # noqa: ANN401 — arbitrary JSON sub-tree
    root: dict[str, Any],
    *,
    _seen: frozenset[str] = frozenset(),
) -> Any:  # noqa: ANN401
    """Recursively inline local ``$ref``s in *node* against *root*.

    Only document-local refs (``#/...``) are supported; external refs (a file
    or URL) raise :class:`CatalogError` — the adapter never fetches the
    network.  Recursive schemas are handled by truncating a ref that is already
    being resolved to ``{}`` so resolution always terminates.

    Args:
        node: A JSON sub-tree (dict / list / scalar).
        root: The full spec document, used as the ref resolution base.
        _seen: Internal cycle-guard set of refs currently on the stack.

    Returns:
        A new structure with local refs inlined.

    Raises:
        CatalogError: On an external / unresolvable ref.
    """
    if isinstance(node, dict):
        ref = node.get("$ref")
        if isinstance(ref, str):
            if not ref.startswith("#/"):
                raise CatalogError(
                    f"OpenAPI external $ref {ref!r} is not supported; "
                    "inline external schemas or use a bundled (local-ref) spec."
                )
            if ref in _seen:
                return {}
            resolved = _resolve_pointer(ref, root)
            return resolve_refs(resolved, root, _seen=_seen | {ref})
        return {k: resolve_refs(v, root, _seen=_seen) for k, v in node.items()}
    if isinstance(node, list):
        return [resolve_refs(item, root, _seen=_seen) for item in node]
    return node


def operation_safety(method: str) -> tuple[list[str], bool]:
    """Map an HTTP method to ``(safety_tags, side_effects)``.

    Mirrors the MCP adapter's annotation handling so downstream card / safety
    handling is uniform: ``GET`` / ``HEAD`` are read-only, ``DELETE`` is
    destructive, and only the read methods are free of side effects.

    Args:
        method: The HTTP method (any case).

    Returns:
        A ``(tags, side_effects)`` tuple.
    """
    m = method.lower()
    tags: list[str] = []
    if m in ("get", "head"):
        tags.append("read-only")
    if m == "delete":
        tags.append("destructive")
    return tags, m not in _READ_METHODS


def compose_args_schema(
    operation: dict[str, Any],
    root: dict[str, Any],
    *,
    shared_parameters: list[Any] | None = None,
) -> dict[str, Any]:
    """Compose ``parameters`` + ``requestBody`` into one object ``args_schema``.

    Composition rule (kept deliberately simple and documented):

    - Every operation / path-level parameter becomes a top-level property keyed
      by its ``name`` (its ``schema`` is used, with the parameter
      ``description`` folded in); ``required`` parameters join ``required``.
    - The ``application/json`` request body (if present) is resolved: when it
      is an object schema whose property names do not collide with parameters,
      its properties are merged in; otherwise the whole body schema is nested
      under a ``"body"`` property to avoid ambiguity.

    Args:
        operation: The resolved operation object.
        root: The full spec, for ref resolution.
        shared_parameters: Path-item-level parameters that apply to every
            operation on the path.

    Returns:
        A JSON-Schema object dict (``{"type": "object", ...}``).  Empty
        ``properties`` yields ``{"type": "object", "properties": {}}``.
    """
    properties: dict[str, Any] = {}
    required: list[str] = []

    raw_params = list(shared_parameters or []) + list(operation.get("parameters") or [])
    for raw in raw_params:
        param = resolve_refs(raw, root)
        if not isinstance(param, dict):
            continue
        name = param.get("name")
        if not isinstance(name, str) or not name:
            continue
        schema = dict(param.get("schema") or {})
        description = param.get("description")
        if isinstance(description, str) and description and "description" not in schema:
            schema["description"] = description
        properties[name] = schema
        if param.get("required"):
            required.append(name)

    _merge_request_body(operation, root, properties, required)

    composed: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        composed["required"] = sorted(set(required))
    return composed


def _merge_request_body(
    operation: dict[str, Any],
    root: dict[str, Any],
    properties: dict[str, Any],
    required: list[str],
) -> None:
    """Fold an operation's JSON request body into *properties* / *required*."""
    request_body = resolve_refs(operation.get("requestBody") or {}, root)
    if not isinstance(request_body, dict):
        return
    content = request_body.get("content")
    if not isinstance(content, dict) or not content:
        return
    media = content.get("application/json")
    if not isinstance(media, dict):
        # Fall back to the first declared media type deterministically.
        _key, media = sorted(content.items())[0]
    body_schema = resolve_refs(media.get("schema") or {}, root) if isinstance(media, dict) else {}
    if not isinstance(body_schema, dict) or not body_schema:
        return

    body_props = body_schema.get("properties")
    is_object = body_schema.get("type") == "object" or isinstance(body_props, dict)
    collides = isinstance(body_props, dict) and bool(set(body_props) & set(properties))
    if is_object and isinstance(body_props, dict) and not collides:
        for key, value in body_props.items():
            properties[key] = value
        body_required = body_schema.get("required")
        if isinstance(body_required, list):
            required.extend(str(r) for r in body_required)
    else:
        properties["body"] = body_schema
        if request_body.get("required"):
            required.append("body")

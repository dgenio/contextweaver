"""Unified identity and collision policy across MCP primitives (#671).

MCP defines three first-class context primitives — tools, resources, and
prompts.  contextweaver historically modelled only tools, with the canonical
``tool_id`` grammar in :mod:`contextweaver.routing.tool_id`
(``docs/gateway_spec.md`` §1).  Extending the gateway to resources and prompts
(#555 / #669 / #670) introduces a cross-primitive identity question: a tool
named ``search`` and a prompt named ``search`` must never collapse to the same
identifier inside one shared :class:`~contextweaver.routing.catalog.Catalog`.

This module is the single source of truth for that policy
(``docs/gateway_spec.md`` §9):

- **Disjoint-by-construction ids.**  Tools keep their bare canonical form
  (``namespace:name[@version][#hash8]``) so existing catalogs, fixtures, and
  the §1 grammar are untouched.  Resources and prompts are tagged with a
  reserved ``kind`` prefix using the ``::`` separator
  (``resource::filesystem:readme#ab12cd34``).  ``::`` cannot appear in a tool
  id (the §1.1 grammar uses a single ``:`` and forbids ``:`` inside the
  namespace/name), so the three id spaces can never overlap.
- **Stable shape hashes.**  Resources hash over their canonical URI; prompts
  hash over the prompt name plus sorted argument names — mirroring how
  :func:`~contextweaver.routing.tool_id.compute_hash8` hashes a tool's input
  schema shape, so prose-only edits never churn an id.
- **Deterministic collision handling.**  When two distinct primitives of the
  same kind still map to the same canonical id, :func:`resolve_collisions`
  appends a deterministic ``~N`` suffix (``N`` ≥ 2) in sorted order, so the
  assignment is reproducible regardless of input ordering.

Public API:
    - :data:`PRIMITIVE_KINDS` — the three primitive kinds.
    - :class:`PrimitiveIdParts` — destructured cross-primitive id.
    - :func:`format_primitive_id` / :func:`parse_primitive_id`.
    - :func:`compute_resource_hash8` / :func:`compute_prompt_hash8`.
    - :func:`canonical_resource_id` / :func:`canonical_prompt_id`.
    - :func:`resolve_collisions` — deterministic ``~N`` disambiguation.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Any, Literal, cast

from contextweaver.exceptions import CatalogError
from contextweaver.routing.tool_id import ToolIdParts, format_tool_id, parse_tool_id

#: The three MCP primitive kinds this module assigns identity to.
PrimitiveKind = Literal["tool", "resource", "prompt"]
PRIMITIVE_KINDS: tuple[PrimitiveKind, ...] = ("tool", "resource", "prompt")

#: Separator between the reserved ``kind`` tag and the tool-form body for
#: non-tool primitives.  Chosen because it cannot occur in a canonical
#: ``tool_id`` (§1.1 uses a single ``:`` and bans ``:`` inside fields), so
#: resource / prompt ids are disjoint from tool ids by construction.
KIND_SEPARATOR = "::"

#: Suffix marker appended by :func:`resolve_collisions` to disambiguate two
#: distinct primitives that would otherwise share a canonical id.
COLLISION_MARKER = "~"


@dataclass(frozen=True)
class PrimitiveIdParts:
    """Destructured form of a cross-primitive identifier.

    A tool id renders bare (``namespace:name#hash8``); a resource or prompt id
    renders with the reserved ``kind`` prefix (``resource::namespace:name#hash8``).

    Attributes:
        kind: One of :data:`PRIMITIVE_KINDS`.
        namespace: Lowercase server-of-origin prefix (§1.1 grammar).
        name: The primitive's derived name (§1.1 grammar).
        version: Optional upstream-declared version string.
        hash8: Optional 8-char lowercase hex shape hash.  Required when
            *version* is ``None`` so the id is stable across prose-only edits.
    """

    kind: PrimitiveKind
    namespace: str
    name: str
    version: str | None = None
    hash8: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict (omitting ``None`` optionals)."""
        payload: dict[str, Any] = {
            "kind": self.kind,
            "namespace": self.namespace,
            "name": self.name,
        }
        if self.version is not None:
            payload["version"] = self.version
        if self.hash8 is not None:
            payload["hash8"] = self.hash8
        return payload

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PrimitiveIdParts:
        """Deserialise from a JSON-compatible dict."""
        kind = data["kind"]
        if kind not in PRIMITIVE_KINDS:
            raise CatalogError(f"primitive kind must be one of {PRIMITIVE_KINDS}, got {kind!r}")
        return cls(
            kind=kind,
            namespace=data["namespace"],
            name=data["name"],
            version=data.get("version"),
            hash8=data.get("hash8"),
        )


def _to_tool_parts(parts: PrimitiveIdParts) -> ToolIdParts:
    """Project the §1 tool-form fields out of *parts* for grammar reuse."""
    return ToolIdParts(
        namespace=parts.namespace,
        name=parts.name,
        version=parts.version,
        hash8=parts.hash8,
    )


def format_primitive_id(parts: PrimitiveIdParts) -> str:
    """Assemble a canonical cross-primitive id from its parts.

    Tools render bare (delegating to
    :func:`~contextweaver.routing.tool_id.format_tool_id`); resources and
    prompts render with the reserved ``kind`` prefix.

    Args:
        parts: A :class:`PrimitiveIdParts` instance.

    Returns:
        The canonical string id.

    Raises:
        CatalogError: If ``kind`` is unknown or any field violates the §1.1
            grammar (the body is validated through the shared ``tool_id``
            helpers so both directions agree).
    """
    if parts.kind not in PRIMITIVE_KINDS:
        raise CatalogError(f"primitive kind must be one of {PRIMITIVE_KINDS}, got {parts.kind!r}")
    body = format_tool_id(_to_tool_parts(parts))
    if parts.kind == "tool":
        return body
    return f"{parts.kind}{KIND_SEPARATOR}{body}"


def parse_primitive_id(s: str) -> PrimitiveIdParts:
    """Split a canonical cross-primitive id into its parts.

    Args:
        s: A canonical id produced by :func:`format_primitive_id`.

    Returns:
        A :class:`PrimitiveIdParts` instance.

    Raises:
        CatalogError: If *s* is not a string, carries an unknown ``kind`` tag,
            or its body violates the §1.1 grammar.
    """
    if not isinstance(s, str):
        raise CatalogError(f"primitive id must be a string, got {type(s).__name__}")
    kind: PrimitiveKind = "tool"
    body = s
    if KIND_SEPARATOR in s:
        prefix, _, body = s.partition(KIND_SEPARATOR)
        if prefix not in PRIMITIVE_KINDS or prefix == "tool":
            raise CatalogError(
                f"primitive id has an unknown kind prefix {prefix!r} "
                f"(expected one of {('resource', 'prompt')})"
            )
        kind = cast(PrimitiveKind, prefix)  # narrowed to resource/prompt by the guard above
    tool_parts = parse_tool_id(body)
    return PrimitiveIdParts(
        kind=kind,
        namespace=tool_parts.namespace,
        name=tool_parts.name,
        version=tool_parts.version,
        hash8=tool_parts.hash8,
    )


def _shape_hash8(canonical: str) -> str:
    """Return the first 8 hex chars of ``sha256(canonical)`` (§1.3 style)."""
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:8]


def compute_resource_hash8(uri: str) -> str:
    """Compute the 8-char shape hash of an MCP resource (§9).

    The hash is taken over the resource's canonical URI — the stable identity
    of a resource per the MCP spec — so a description- or title-only update
    does not churn the id.

    Args:
        uri: The resource's ``uri`` field (e.g. ``file:///docs/readme.md``).

    Returns:
        An 8-character lowercase hex string.
    """
    return _shape_hash8("resource\n" + (uri or ""))


def compute_prompt_hash8(name: str, argument_names: list[str] | None) -> str:
    """Compute the 8-char shape hash of an MCP prompt (§9).

    The hash is taken over the prompt name plus its **sorted** argument names,
    mirroring how a tool hashes its input-schema property keys: the argument
    *set* defines the prompt's call shape, while prose (descriptions, the
    rendered messages) does not affect identity.

    Args:
        name: The upstream prompt name.
        argument_names: The prompt's declared argument names, or ``None``.

    Returns:
        An 8-character lowercase hex string.
    """
    canonical = name + "\n" + json.dumps(sorted(argument_names or []), separators=(",", ":"))
    return _shape_hash8("prompt\n" + canonical)


def canonical_resource_id(*, namespace: str, name: str, uri: str) -> str:
    """Assemble a canonical resource id from upstream metadata.

    Args:
        namespace: Lowercase server-of-origin prefix.
        name: Derived resource name (§1.4 rules, applied by the caller).
        uri: The resource URI; drives ``hash8`` so two resources sharing a
            name but different URIs produce distinct ids.

    Returns:
        The canonical ``resource::…`` id string.
    """
    return format_primitive_id(
        PrimitiveIdParts(
            kind="resource",
            namespace=namespace,
            name=name,
            hash8=compute_resource_hash8(uri),
        )
    )


def canonical_prompt_id(*, namespace: str, name: str, argument_names: list[str] | None) -> str:
    """Assemble a canonical prompt id from upstream metadata.

    Args:
        namespace: Lowercase server-of-origin prefix.
        name: Derived prompt name (§1.4 rules, applied by the caller).
        argument_names: The prompt's declared argument names, or ``None``.

    Returns:
        The canonical ``prompt::…`` id string.
    """
    return format_primitive_id(
        PrimitiveIdParts(
            kind="prompt",
            namespace=namespace,
            name=name,
            hash8=compute_prompt_hash8(name, argument_names),
        )
    )


def resolve_collisions(ids: list[str]) -> dict[str, str]:
    """Deterministically disambiguate repeated ids (§9 collision policy).

    The lowest input index of each repeated id keeps the bare form; subsequent
    occurrences (in ascending index order) receive a ``~N`` suffix (``N`` ≥ 2).
    The assignment is deterministic *for a given input order* — it depends only
    on the list indexes, never on dict iteration order, so it reproduces across
    runs.  It is **not** order-independent: re-ordering ``ids`` changes which
    occurrence keeps the bare id.  Callers should de-duplicate ids that refer to
    the *same* primitive first; every entry here is a distinct primitive.

    Args:
        ids: Canonical ids in catalog order (duplicates allowed).

    Returns:
        A dict keyed by a ``"<index>"`` token → the unique id, so callers can
        re-key their items.  Index tokens are stable across runs.

    Example:
        >>> resolve_collisions(["fs:readme#ab12cd34", "fs:readme#ab12cd34"])
        {'0': 'fs:readme#ab12cd34', '1': 'fs:readme#ab12cd34~2'}
    """
    # Group input positions by their canonical id so we can number deterministically.
    positions_by_id: dict[str, list[int]] = {}
    for index, raw in enumerate(ids):
        positions_by_id.setdefault(raw, []).append(index)
    assignment: dict[str, str] = {}
    for raw, positions in positions_by_id.items():
        # Sorted so the lowest index keeps the bare id; later ones get ~2, ~3, ….
        for ordinal, index in enumerate(sorted(positions)):
            unique = raw if ordinal == 0 else f"{raw}{COLLISION_MARKER}{ordinal + 1}"
            assignment[str(index)] = unique
    return assignment

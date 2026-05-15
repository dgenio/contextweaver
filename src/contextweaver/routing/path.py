"""``tool_browse`` path-navigation grammar (§3.2).

Parses and validates hierarchical paths through a :class:`ChoiceGraph` for
the gateway's ``tool_browse(path="/...")`` surface.  Grammar::

    path     = "/" [ segment ( "/" segment )* ]
    segment  = ( [a-z0-9] [a-z0-9_-]{0,63} ) | "*"

- A bare ``/`` lists root-level cards (one per namespace).
- A trailing ``/`` is invalid.
- Empty segments are invalid (``//foo``).
- The segment ``*`` lists all children at that level.
- Segments are case-sensitive lowercase; root-level segments must begin
  with ``[a-z]`` (deeper segments may begin with a digit per §3.2).

Public API:
    - :func:`parse_path` — string → list of segments (raises
      :class:`PathInvalidError` on grammar violations).
    - :func:`resolve_path` — segments + :class:`ChoiceGraph` → list of
      node IDs (raises :class:`PathNotFoundError` if the path does not
      resolve).
"""

from __future__ import annotations

import re

from contextweaver.exceptions import PathInvalidError, PathNotFoundError
from contextweaver.routing.graph import ChoiceGraph

_ROOT_SEGMENT_RE = re.compile(r"^[a-z][a-z0-9_\-]{0,63}$")
_DEEP_SEGMENT_RE = re.compile(r"^[a-z0-9][a-z0-9_\-]{0,63}$")
_WILDCARD = "*"


def parse_path(path: str) -> list[str]:
    """Validate and split a ``tool_browse`` path into its segments (§3.2).

    Args:
        path: The path string.  A bare ``"/"`` is valid and returns ``[]``.

    Returns:
        The list of segments (excluding the leading ``"/"``).  An empty
        list means "root".

    Raises:
        PathInvalidError: For any §3.2 grammar violation (missing leading
            ``"/"``, trailing ``"/"``, empty segment, malformed segment,
            wildcard in a non-final position).
    """
    if not isinstance(path, str):
        raise PathInvalidError(f"path must be a string, got {type(path).__name__}")
    if not path:
        raise PathInvalidError("path must not be empty")
    if not path.startswith("/"):
        raise PathInvalidError(f"path must start with '/' (got {path!r})")
    if path == "/":
        return []
    if path.endswith("/"):
        raise PathInvalidError(f"path must not end with '/' (got {path!r})")

    segments = path[1:].split("/")
    for i, seg in enumerate(segments):
        if not seg:
            raise PathInvalidError(f"empty segment at index {i} in {path!r}")
        if seg == _WILDCARD:
            if i != len(segments) - 1:
                raise PathInvalidError(
                    f"wildcard '*' may only appear as the final segment in {path!r}"
                )
            continue
        if i == 0:
            if not _ROOT_SEGMENT_RE.match(seg):
                raise PathInvalidError(f"root segment {seg!r} must start with [a-z] (§3.2)")
        else:
            if not _DEEP_SEGMENT_RE.match(seg):
                raise PathInvalidError(f"segment {seg!r} not matching §3.2 grammar")
    return segments


def resolve_path(graph: ChoiceGraph, segments: list[str]) -> list[str]:
    """Walk *graph* down *segments* and return the addressed child IDs.

    Behaviour by path shape:

    - Empty *segments* → IDs of the root-level navigation children
      (typically one per namespace).
    - Final segment is :data:`_WILDCARD` (``"*"``) → all children at the
      parent level, same as omitting the segment.
    - Last segment matches a leaf node → ``[leaf_id]``.
    - Last segment matches an interior node → that node's children.

    Args:
        graph: A :class:`~contextweaver.routing.graph.ChoiceGraph`.
        segments: Output of :func:`parse_path`.

    Returns:
        Either the list of child IDs of the addressed node, or
        ``[leaf_id]`` when the path lands on a leaf item.

    Raises:
        PathNotFoundError: If any segment does not resolve to a child of
            the current node (and is not the wildcard ``"*"``).
    """
    root_id = graph.root_id
    if not segments:
        return graph.successors(root_id)

    current_parent = root_id
    walked: list[str] = []
    final_id: str | None = None

    for seg in segments:
        walked.append(seg)
        children = graph.successors(current_parent)
        if seg == _WILDCARD:
            # parse_path already enforces wildcard is in the final position.
            return children

        match = _match_segment(seg, children)
        if match is None:
            raise PathNotFoundError(f"path /{'/'.join(walked)} does not resolve in the catalog")
        final_id = match
        current_parent = match

    next_children = graph.successors(current_parent)
    if not next_children and final_id is not None:
        return [final_id]
    return next_children


def _match_segment(segment: str, child_ids: list[str]) -> str | None:
    """Return the *child_ids* entry whose path-segment label equals *segment*.

    The path-segment label of a child ID is:

    - The portion after the last ``/`` for hierarchical navigation node
      IDs like ``root/github/issues`` (segment label = ``issues``).
    - The portion before the first ``:`` for canonical ``tool_id`` leaves
      like ``github:create_issue@1.4.0`` (segment label = ``github``).  In
      practice canonical leaves only match deeper segments because root
      segments are addressed by their namespace node, not their leaf id.
    - Otherwise the entire ID, lowercased.

    Matching is case-insensitive on the *child_ids* side because graph
    construction may produce mixed-case labels for navigation nodes while
    §3.2 mandates lowercase path segments.
    """
    target = segment.lower()
    for child_id in child_ids:
        if _segment_label_for(child_id) == target:
            return child_id
    return None


def _segment_label_for(child_id: str) -> str:
    """Return the canonical lowercase path-segment label for a child ID.

    Two ID shapes are recognised:

    - Hierarchical navigation node IDs like ``root/github/issues`` →
      segment label = ``issues`` (text after the final ``"/"``).
    - Canonical ``tool_id`` leaves like ``github:create_issue@1.0#abcdef01``
      → segment label = ``create_issue`` (the ``name`` part: text
      between the first ``":"`` and the first ``"@"`` or ``"#"``).

    Any other shape lowercases the entire ID.
    """
    if "/" in child_id:
        return child_id.rsplit("/", 1)[-1].lower()
    if ":" in child_id:
        rest = child_id.split(":", 1)[1]
        for sep in ("@", "#"):
            if sep in rest:
                rest = rest.split(sep, 1)[0]
        return rest.lower()
    return child_id.lower()

"""File I/O helpers for :class:`~contextweaver.routing.graph.ChoiceGraph`.

Provides standalone :func:`save_graph` and :func:`load_graph` functions
so that the main ``graph`` module stays focused on the DAG data structure.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from contextweaver.exceptions import GraphBuildError

if TYPE_CHECKING:
    from contextweaver.routing.graph import ChoiceGraph


def save_graph(graph: ChoiceGraph, path: str | Path) -> None:
    """Write *graph* to a JSON file with deterministic formatting.

    Args:
        graph: The :class:`ChoiceGraph` to persist.
        path: Filesystem path for the output file.
    """
    Path(path).write_text(
        json.dumps(graph.to_dict(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def load_graph(path: str | Path) -> ChoiceGraph:
    """Load a graph from a JSON file and validate it.

    Validates: root_id exists, all child refs resolve, no cycles (DFS),
    all items reachable from root.

    Args:
        path: Filesystem path to a JSON file.

    Returns:
        A validated :class:`ChoiceGraph`.

    Raises:
        GraphBuildError: If the file is invalid or the graph fails validation.
    """
    # Import here to avoid circular dependency at module level.
    from contextweaver.routing.graph import ChoiceGraph

    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise GraphBuildError(f"Cannot read graph file: {exc}") from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise GraphBuildError(f"Invalid JSON in graph file: {exc}") from exc

    graph = ChoiceGraph.from_dict(data)
    graph._validate()  # noqa: SLF001
    return graph

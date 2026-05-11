"""File I/O helpers for :class:`~contextweaver.routing.graph.ChoiceGraph`.

Provides standalone :func:`save_graph` and :func:`load_graph` functions
so that the main ``graph`` module stays focused on the DAG data structure.

Supports both JSON and YAML formats; the format is auto-detected from the
file extension (``.yaml`` / ``.yml`` → YAML, everything else → JSON).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from contextweaver.exceptions import GraphBuildError

if TYPE_CHECKING:
    from contextweaver.routing.graph import ChoiceGraph


def _is_yaml_path(path: str | Path) -> bool:
    return Path(path).suffix.lower() in (".yaml", ".yml")


def save_graph(graph: ChoiceGraph, path: str | Path) -> None:
    """Write *graph* to a JSON or YAML file with deterministic formatting.

    The format is selected by the file extension: ``.yaml`` / ``.yml`` for
    YAML, anything else for JSON. Output is always sorted by key to
    preserve deterministic-by-default behaviour.

    Args:
        graph: The :class:`ChoiceGraph` to persist.
        path: Filesystem path for the output file.
    """
    data = graph.to_dict()
    if _is_yaml_path(path):
        import yaml  # core dep — see pyproject.toml

        text = yaml.safe_dump(data, sort_keys=True, default_flow_style=False)
    else:
        text = json.dumps(data, indent=2, sort_keys=True) + "\n"
    Path(path).write_text(text, encoding="utf-8")


def load_graph(path: str | Path) -> ChoiceGraph:
    """Load a graph from a JSON or YAML file and validate it.

    The format is selected by the file extension. Validates: root_id
    exists, all child refs resolve, no cycles (DFS), all items reachable
    from root.

    Args:
        path: Filesystem path to a JSON or YAML file.

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

    if _is_yaml_path(path):
        import yaml  # core dep — see pyproject.toml

        try:
            data = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise GraphBuildError(f"Invalid YAML in graph file: {exc}") from exc
    else:
        try:
            data = json.loads(text)
        except json.JSONDecodeError as exc:
            raise GraphBuildError(f"Invalid JSON in graph file: {exc}") from exc

    graph = ChoiceGraph.from_dict(data)
    graph._validate()  # noqa: SLF001
    return graph

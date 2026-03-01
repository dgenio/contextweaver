"""Routing tree builder for contextweaver.

Converts a flat :class:`~contextweaver.routing.catalog.Catalog` into a
:class:`~contextweaver.routing.graph.ChoiceGraph` by grouping items under
namespace / category nodes derived from the
:class:`~contextweaver.routing.labeler.KeywordLabeler`.
"""

from __future__ import annotations

from contextweaver.routing.catalog import Catalog
from contextweaver.routing.graph import ChoiceGraph
from contextweaver.routing.labeler import KeywordLabeler
from contextweaver.types import SelectableItem


class TreeBuilder:
    """Build a two-level :class:`~contextweaver.routing.graph.ChoiceGraph` from a catalog.

    Structure::

        root → namespace_or_category → item_id

    If an item has a non-empty ``namespace`` that is used as the intermediate
    node; otherwise the category label from :class:`~contextweaver.routing.labeler.KeywordLabeler`
    is used.
    """

    def __init__(self, labeler: KeywordLabeler | None = None) -> None:
        self._labeler = labeler or KeywordLabeler()

    def build(self, catalog: Catalog) -> ChoiceGraph:
        """Build a :class:`~contextweaver.routing.graph.ChoiceGraph` from *catalog*.

        Args:
            catalog: The populated tool catalog.

        Returns:
            A two-level DAG rooted at ``"root"``.
        """
        graph = ChoiceGraph()
        graph.add_node("root")

        for item in catalog.all():
            group = self._group_for(item)
            if group not in graph.nodes():
                graph.add_node(group)
                graph.add_edge("root", group)
            graph.add_node(item.id)
            graph.add_edge(group, item.id)

        return graph

    def _group_for(self, item: SelectableItem) -> str:
        """Return the intermediate node name for *item*."""
        if item.namespace:
            return f"ns:{item.namespace}"
        category, _ = self._labeler.label(item)
        return f"cat:{category}"

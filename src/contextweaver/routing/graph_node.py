"""ChoiceNode dataclass for the contextweaver Routing Engine.

A :class:`ChoiceNode` represents a single node in the routing
:class:`~contextweaver.routing.graph.ChoiceGraph` DAG.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class ChoiceNode:
    """A single node in the routing :class:`~contextweaver.routing.graph.ChoiceGraph`.

    Attributes:
        node_id: Unique identifier for this node.
        label: Short human-readable label shown during routing.
        routing_hint: A sentence describing what this group of children is about.
        children: Ordered list of child IDs (both nodes and items).
        child_types: Mapping of child ID to ``"node"`` or ``"item"``.
        stats: Arbitrary statistics dict (populated by :meth:`ChoiceGraph.stats`).
    """

    node_id: str
    label: str = ""
    routing_hint: str = ""
    children: list[str] = field(default_factory=list)
    child_types: dict[str, str] = field(default_factory=dict)
    stats: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "node_id": self.node_id,
            "label": self.label,
            "routing_hint": self.routing_hint,
            "children": list(self.children),
            "child_types": dict(self.child_types),
            "stats": dict(self.stats),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ChoiceNode:
        """Deserialise from a JSON-compatible dict."""
        return cls(
            node_id=data["node_id"],
            label=data.get("label", ""),
            routing_hint=data.get("routing_hint", ""),
            children=list(data.get("children", [])),
            child_types=dict(data.get("child_types", {})),
            stats=dict(data.get("stats", {})),
        )

"""Single-kind routing index for the primitive gateway runtime (#669 / #670).

Private helper extracted from
:mod:`contextweaver.adapters.gateway_primitives` to keep that module within the
≤300-line convention.  :class:`PrimitiveIndex` wraps one
:class:`~contextweaver.routing.catalog.Catalog` +
:class:`~contextweaver.routing.graph.ChoiceGraph` +
:class:`~contextweaver.routing.router.Router` for a single primitive kind
(resources *or* prompts) and exposes a bounded ``browse`` over it.  Not public
API.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from contextweaver.adapters._bounded_browse import bounded_browse
from contextweaver.adapters.gateway_error import GatewayError
from contextweaver.envelope import ChoiceCard
from contextweaver.routing.catalog import Catalog
from contextweaver.routing.graph import ChoiceGraph
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import SelectableItem


@dataclass
class PrimitiveIndex:
    """A single-kind catalog + routing graph + router with bounded browse.

    Args:
        redact_secrets: When ``True`` (#428/#743) prompt-bound card text is
            scrubbed via :func:`~contextweaver.secrets.scrub_secrets`, matching
            the tool runtime so resource/prompt cards are never a scrub gap.
        surface: Meta-tool name used in ``ARGS_INVALID`` messages
            (e.g. ``"resource_browse"`` / ``"prompt_browse"``).
    """

    beam_width: int = 3
    top_k: int = 10
    redact_secrets: bool = False
    surface: str = "browse"
    catalog: Catalog = field(default_factory=Catalog)
    graph: ChoiceGraph | None = None
    router: Router | None = None

    def rebuild(self, items: list[SelectableItem]) -> int:
        """Rebuild the catalog/graph/router from *items*; return the count."""
        self.catalog = Catalog()
        for item in items:
            self.catalog.register(item)
        if items:
            self.graph = TreeBuilder().build(items)
            self.router = Router(
                self.graph, items=items, beam_width=self.beam_width, top_k=self.top_k
            )
        else:
            self.graph = None
            self.router = None
        return len(items)

    def browse(
        self, *, query: str | None, path: str | None, top_k: int | None
    ) -> list[ChoiceCard] | GatewayError:
        """Browse this index by *query* (routed) or *path* (graph navigation).

        Delegates to the shared :func:`~contextweaver.adapters._bounded_browse.bounded_browse`
        core so tool and primitive card production — including ``redact_secrets``
        scrubbing — stay a single implementation (#743).
        """
        # `top_k` arrives straight from an MCP client; reject non-integer or
        # non-positive values here rather than letting a bad type reach
        # make_choice_cards and raise TypeError across the meta-tool boundary.
        # (Kept in the primitive wrapper so the tool path's behavior is
        # unchanged by the shared-helper extraction, #743.)
        if top_k is not None and (
            isinstance(top_k, bool) or not isinstance(top_k, int) or top_k < 1
        ):
            return GatewayError(
                code="ARGS_INVALID",
                message="'top_k' must be a positive integer.",
            )
        return bounded_browse(
            router=self.router,
            graph=self.graph,
            catalog=self.catalog,
            query=query,
            path=path,
            top_k=top_k,
            default_top_k=self.top_k,
            redact_secrets=self.redact_secrets,
            surface=self.surface,
        )

"""Golden fixture for route-prompt construction (issue #296).

Pins the byte-stable output of ``ContextManager.build_route_prompt_sync``
against a checked-in fixture.  Catches drift in:

* prompt rendering (header / footer / card text)
* route-result serialisation (``RouteResult.to_dict``)
* trace shape (``RouteTrace.to_dict``)
* ``ChoiceCard.to_dict`` field ordering / values
* ``BuildStats.to_dict`` content

Volatile fields are normalised by ``tests.fixtures._normalize`` so the
fixture is byte-stable across runs and machines.
"""

from __future__ import annotations

from pathlib import Path

from contextweaver.config import ContextBudget, ContextPolicy
from contextweaver.context.manager import ContextManager
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.store.artifacts import InMemoryArtifactStore
from contextweaver.store.event_log import InMemoryEventLog
from contextweaver.types import ContextItem, ItemKind, SelectableItem
from tests.fixtures._normalize import load_fixture, normalize, to_canonical_json

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "golden" / "route_prompt"


def _basic_scenario() -> tuple[ContextManager, Router, str, str]:
    """Reproduce the deterministic scenario captured in ``basic.json``."""
    items = [
        SelectableItem(
            id="db_read",
            kind="tool",
            name="read_db",
            description="Read records from the customer database",
            tags=["data", "read"],
            namespace="db",
        ),
        SelectableItem(
            id="db_write",
            kind="tool",
            name="write_db",
            description="Write records to the customer database",
            tags=["data", "write"],
            namespace="db",
        ),
        SelectableItem(
            id="send_email",
            kind="tool",
            name="send_email",
            description="Send an email notification",
            tags=["comm", "email"],
            namespace="comm",
        ),
        SelectableItem(
            id="search_docs",
            kind="tool",
            name="search_docs",
            description="Search documentation pages",
            tags=["search", "docs"],
            namespace="search",
        ),
    ]
    graph = TreeBuilder(max_children=10).build(items)
    router = Router(graph, items=items, beam_width=2, top_k=3, confidence_gap=0.15)

    log = InMemoryEventLog()
    log.append(
        ContextItem(
            id="u1",
            kind=ItemKind.user_turn,
            text="Please find customer 12345 in the database.",
        )
    )
    log.append(
        ContextItem(
            id="a1",
            kind=ItemKind.agent_msg,
            text="Looking up the customer now.",
        )
    )
    mgr = ContextManager(
        event_log=log,
        artifact_store=InMemoryArtifactStore(),
        budget=ContextBudget(route=300, call=300, interpret=300, answer=500),
        policy=ContextPolicy(),
    )
    return (
        mgr,
        router,
        "Find a customer record and email them a receipt",
        "find customer in database",
    )


def test_route_prompt_basic_matches_golden() -> None:
    mgr, router, goal, query = _basic_scenario()
    pack, cards, route_result = mgr.build_route_prompt_sync(goal=goal, query=query, router=router)

    actual = normalize(
        {
            "route_result": route_result.to_dict(include_items=False),
            "cards": [c.to_dict() for c in cards],
            "pack": {
                "prompt": pack.prompt,
                "phase": pack.phase.value,
                "stats": pack.stats.to_dict(),
            },
        },
        drop_keys=("retriever_engine",),
    )
    expected = normalize(load_fixture(FIXTURE_DIR / "basic.json"), drop_keys=("retriever_engine",))

    if actual != expected:
        diff = (
            f"\n--- expected ({FIXTURE_DIR / 'basic.json'}):\n"
            + to_canonical_json(expected)
            + "\n--- actual:\n"
            + to_canonical_json(actual)
        )
        raise AssertionError(f"route-prompt golden drifted: {FIXTURE_DIR / 'basic.json'}\n{diff}")


def test_route_prompt_basic_is_deterministic() -> None:
    """Two builds against the same scenario produce byte-identical
    normalised output — guards against introducing implicit
    nondeterminism in the pipeline."""
    mgr, router, goal, query = _basic_scenario()
    pack1, cards1, rr1 = mgr.build_route_prompt_sync(goal=goal, query=query, router=router)
    mgr2, router2, _, _ = _basic_scenario()
    pack2, cards2, rr2 = mgr2.build_route_prompt_sync(goal=goal, query=query, router=router2)

    a = normalize(
        {
            "route_result": rr1.to_dict(include_items=False),
            "cards": [c.to_dict() for c in cards1],
            "pack": {"prompt": pack1.prompt, "stats": pack1.stats.to_dict()},
        },
        drop_keys=("retriever_engine",),
    )
    b = normalize(
        {
            "route_result": rr2.to_dict(include_items=False),
            "cards": [c.to_dict() for c in cards2],
            "pack": {"prompt": pack2.prompt, "stats": pack2.stats.to_dict()},
        },
        drop_keys=("retriever_engine",),
    )
    assert a == b

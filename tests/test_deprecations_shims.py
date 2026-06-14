"""Deprecation coverage for the pre-1.0 legacy compatibility shims (issue #642).

Each runtime-deprecated shim must (a) emit a ``DeprecationWarning`` that names
its replacement and (b) behave identically to before.  A final guard asserts
that the canonical, non-legacy code paths the library and its docs use emit
*no* contextweaver deprecation warning, so the escalated ``filterwarnings``
gate (see ``pyproject.toml``) protects in-repo callers.
"""

from __future__ import annotations

import importlib
import warnings

import pytest

from contextweaver._deprecation import DEPRECATION_MESSAGE_PREFIX
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder
from contextweaver.types import SelectableItem


def _items() -> list[SelectableItem]:
    return [
        SelectableItem(id="db_read", kind="tool", name="read_db", description="Read database"),
        SelectableItem(id="db_write", kind="tool", name="write_db", description="Write database"),
        SelectableItem(id="email", kind="tool", name="send_email", description="Send an email"),
    ]


def _router() -> Router:
    items = _items()
    graph = TreeBuilder(max_children=20).build(items)
    return Router(graph, items=items, top_k=20)


# ------------------------------------------------------------------
# ToolCard alias
# ------------------------------------------------------------------


def test_toolcard_top_level_alias_warns_and_resolves() -> None:
    import contextweaver

    with pytest.warns(DeprecationWarning, match="ToolCard is deprecated"):
        alias = contextweaver.ToolCard
    assert alias is SelectableItem


def test_toolcard_types_alias_warns_and_resolves() -> None:
    types_mod = importlib.import_module("contextweaver.types")

    with pytest.warns(DeprecationWarning, match="Use SelectableItem instead"):
        alias = types_mod.ToolCard
    assert alias is SelectableItem


def test_types_unknown_attribute_still_raises_attribute_error() -> None:
    types_mod = importlib.import_module("contextweaver.types")
    with pytest.raises(AttributeError, match="no attribute 'NotAThing'"):
        _ = types_mod.NotAThing


# ------------------------------------------------------------------
# RouteResult.debug_trace / RouteTrace.to_legacy_dicts
# ------------------------------------------------------------------


def test_debug_trace_warns_but_matches_structured_trace() -> None:
    result = _router().route("database", debug=True)
    with pytest.warns(DeprecationWarning, match="RouteResult.debug_trace is deprecated"):
        legacy = result.debug_trace
    # Behaviour identical to the (non-deprecated) private constructor.
    assert legacy == result.trace._to_legacy_dicts()


def test_to_legacy_dicts_warns_but_matches_private_helper() -> None:
    trace = _router().route("database", debug=True).trace
    with pytest.warns(DeprecationWarning, match="RouteTrace.to_legacy_dicts is deprecated"):
        legacy = trace.to_legacy_dicts()
    assert legacy == trace._to_legacy_dicts()


# ------------------------------------------------------------------
# Router(scorer=...) legacy constructor path
# ------------------------------------------------------------------


def test_router_scorer_kwarg_warns_but_still_routes() -> None:
    from contextweaver._utils import TfIdfScorer

    items = _items()
    graph = TreeBuilder(max_children=20).build(items)
    with pytest.warns(DeprecationWarning, match=r"Router\(scorer=\.\.\.\) is deprecated"):
        router = Router(graph, items=items, scorer=TfIdfScorer(), top_k=20)
    result = router.route("database")
    assert result.candidate_ids  # legacy scorer path still produces candidates


# ------------------------------------------------------------------
# Canonical (non-legacy) path is warning-clean
# ------------------------------------------------------------------


def test_canonical_routing_path_emits_no_first_party_deprecation() -> None:
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        router = _router()
        result = router.route("database")
        _ = result.trace.to_dict()
        _ = result.trace.steps
        _ = result.to_dict()

    offenders = [
        str(w.message)
        for w in caught
        if issubclass(w.category, DeprecationWarning)
        and str(w.message).startswith(DEPRECATION_MESSAGE_PREFIX)
    ]
    assert offenders == []

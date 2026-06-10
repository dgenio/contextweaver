"""Tests for contextweaver.adapters.proxy_runtime (#29).

Exercises the shared core that both the transparent proxy (#13) and the
two-tool gateway (#28) build on.  ``StubUpstream`` lets us drive every
path without spinning up an MCP server.
"""

from __future__ import annotations

from typing import Any

import pytest

from contextweaver.adapters.gateway_error import GatewayError
from contextweaver.adapters.mcp_upstream import StubUpstream
from contextweaver.adapters.proxy_runtime import CACHE_BREAKPOINT_ID, ProxyRuntime
from contextweaver.envelope import ChoiceCard, HydrationResult, ResultEnvelope

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _tool_defs() -> list[dict[str, Any]]:
    return [
        {
            "name": "github.create_issue",
            "description": "Open a new GitHub issue.",
            "inputSchema": {
                "type": "object",
                "properties": {"title": {"type": "string"}, "body": {"type": "string"}},
                "required": ["title"],
                "additionalProperties": False,
            },
            "_meta": {"version": "1.4.0"},
        },
        {
            "name": "github.close_issue",
            "description": "Close an open GitHub issue.",
            "inputSchema": {
                "type": "object",
                "properties": {"issue_id": {"type": "integer"}},
                "required": ["issue_id"],
                "additionalProperties": False,
            },
            "_meta": {"version": "1.4.0"},
        },
        {
            "name": "slack_send_message",
            "description": "Post a message to a Slack channel.",
            "inputSchema": {
                "type": "object",
                "properties": {"channel": {"type": "string"}, "text": {"type": "string"}},
                "required": ["channel", "text"],
            },
        },
    ]


async def _ok_handler(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": f"called {name} with {sorted(args.keys())}"}],
        "isError": False,
    }


def _make_runtime() -> ProxyRuntime:
    runtime = ProxyRuntime(StubUpstream(_tool_defs(), handler=_ok_handler))
    runtime.register_tool_defs_sync(_tool_defs())
    return runtime


# ---------------------------------------------------------------------------
# Catalog management
# ---------------------------------------------------------------------------


def test_register_tool_defs_sync_populates_catalog() -> None:
    runtime = _make_runtime()
    ids = runtime.list_tool_ids()
    assert len(ids) == 3
    # Canonical tool_id shape: namespace:name@version or namespace:name#hash8
    assert any(i.startswith("github:") for i in ids)
    assert any(i.startswith("slack:") for i in ids)


async def test_refresh_catalog_uses_upstream() -> None:
    runtime = ProxyRuntime(StubUpstream(_tool_defs()))
    n = await runtime.refresh_catalog()
    assert n == 3
    assert len(runtime.list_tool_ids()) == 3


# ---------------------------------------------------------------------------
# browse() — §3.1 mutual exclusion + query / path
# ---------------------------------------------------------------------------


def test_browse_requires_exactly_one_arg() -> None:
    runtime = _make_runtime()
    err = runtime.browse()
    assert isinstance(err, GatewayError)
    assert err.code == "ARGS_INVALID"


def test_browse_rejects_both_query_and_path() -> None:
    runtime = _make_runtime()
    err = runtime.browse(query="issue", path="/github")
    assert isinstance(err, GatewayError)
    assert err.code == "ARGS_INVALID"


def test_browse_by_query_returns_cards() -> None:
    runtime = _make_runtime()
    cards = runtime.browse(query="open a github issue")
    assert isinstance(cards, list)
    assert all(isinstance(c, ChoiceCard) for c in cards)
    assert len(cards) > 0


def test_browse_by_path_root_lists_namespaces() -> None:
    runtime = _make_runtime()
    cards = runtime.browse(path="/")
    assert isinstance(cards, list)
    assert len(cards) >= 1


def test_browse_by_path_invalid_returns_path_invalid() -> None:
    runtime = _make_runtime()
    err = runtime.browse(path="bad-path-no-leading-slash")
    assert isinstance(err, GatewayError)
    assert err.code == "PATH_INVALID"


def test_browse_by_path_unknown_returns_path_not_found() -> None:
    runtime = _make_runtime()
    err = runtime.browse(path="/no_such_namespace")
    assert isinstance(err, GatewayError)
    assert err.code == "PATH_NOT_FOUND"


# ---------------------------------------------------------------------------
# hydrate() — §4.1
# ---------------------------------------------------------------------------


def test_hydrate_returns_full_schema() -> None:
    runtime = _make_runtime()
    tool_id = next(i for i in runtime.list_tool_ids() if i.startswith("github:create_issue"))
    hydrated = runtime.hydrate(tool_id)
    assert isinstance(hydrated, HydrationResult)
    assert "title" in hydrated.args_schema.get("properties", {})


def test_hydrate_unknown_returns_hydrate_failed() -> None:
    runtime = _make_runtime()
    err = runtime.hydrate("does:not_exist#deadbeef")
    assert isinstance(err, GatewayError)
    assert err.code == "HYDRATE_FAILED"


# ---------------------------------------------------------------------------
# execute() — §4.2 + §4.4
# ---------------------------------------------------------------------------


async def test_execute_validates_args_against_schema() -> None:
    runtime = _make_runtime()
    tool_id = next(i for i in runtime.list_tool_ids() if i.startswith("github:create_issue"))
    # Missing required field "title" → ARGS_INVALID.
    err = await runtime.execute(tool_id, {})
    assert isinstance(err, GatewayError)
    assert err.code == "ARGS_INVALID"


async def test_execute_happy_path_returns_envelope() -> None:
    runtime = _make_runtime()
    tool_id = next(i for i in runtime.list_tool_ids() if i.startswith("github:create_issue"))
    result = await runtime.execute(tool_id, {"title": "Bug report"})
    assert isinstance(result, ResultEnvelope)
    assert result.status == "ok"
    assert result.provenance.get("tool_id") == tool_id


async def test_execute_unknown_tool_returns_hydrate_failed() -> None:
    runtime = _make_runtime()
    err = await runtime.execute("missing:tool#00000000", {})
    assert isinstance(err, GatewayError)
    assert err.code == "HYDRATE_FAILED"


async def test_execute_upstream_failure_returns_upstream_error() -> None:
    async def boom(name: str, args: dict[str, Any]) -> dict[str, Any]:
        raise RuntimeError("transport collapsed")

    runtime = ProxyRuntime(StubUpstream(_tool_defs(), handler=boom))
    runtime.register_tool_defs_sync(_tool_defs())
    tool_id = next(i for i in runtime.list_tool_ids() if i.startswith("github:create_issue"))
    err = await runtime.execute(tool_id, {"title": "x"})
    assert isinstance(err, GatewayError)
    assert err.code == "UPSTREAM_ERROR"
    assert "transport collapsed" in err.message


# ---------------------------------------------------------------------------
# view() — #34
# ---------------------------------------------------------------------------


async def test_view_drilldown_returns_slice() -> None:
    """tool_view returns a drilldown slice over a stored artifact."""
    long_text = "line one\nline two\nline three\nline four\nline five"

    async def textual(name: str, args: dict[str, Any]) -> dict[str, Any]:
        return {
            "content": [{"type": "text", "text": long_text}],
            "isError": False,
        }

    defs = _tool_defs()
    runtime = ProxyRuntime(StubUpstream(defs, handler=textual))
    runtime.register_tool_defs_sync(defs)
    tool_id = next(i for i in runtime.list_tool_ids() if i.startswith("github:create_issue"))
    envelope = await runtime.execute(tool_id, {"title": "x"})
    assert isinstance(envelope, ResultEnvelope)
    assert len(envelope.artifacts) == 1
    handle = envelope.artifacts[0].handle
    assert handle.startswith(f"text:{tool_id}:")
    assert runtime.context_manager.artifact_store.exists(handle)
    sliced = runtime.view(handle, {"type": "head", "chars": 8})
    assert sliced == "line one"


def test_view_unknown_handle_returns_view_failed() -> None:
    runtime = _make_runtime()
    err = runtime.view("no_such_handle", {"type": "head", "n_chars": 10})
    assert isinstance(err, GatewayError)
    assert err.code == "VIEW_FAILED"


# ---------------------------------------------------------------------------
# strip_tools_list() — §4.1
# ---------------------------------------------------------------------------


def test_strip_tools_list_emits_sentinel_input_schema() -> None:
    runtime = _make_runtime()
    stripped = runtime.strip_tools_list()
    assert len(stripped) == 3
    for entry in stripped:
        assert entry["inputSchema"] == {"type": "object"}
        # No banned fields per §2.2.
        for banned in ("args_schema", "outputSchema", "output_schema", "annotations", "_meta"):
            assert banned not in entry


# ---------------------------------------------------------------------------
# cache_stable browse — §5
# ---------------------------------------------------------------------------


def _make_cache_stable_runtime() -> ProxyRuntime:
    """Like ``_make_runtime`` but with ``cache_stable=True``."""
    runtime = ProxyRuntime(StubUpstream(_tool_defs(), handler=_ok_handler), cache_stable=True)
    runtime.register_tool_defs_sync(_tool_defs())
    return runtime


def _ids(cards: object) -> list[str]:
    """Extract the ``id`` of every card; assert the response was not an error."""
    assert isinstance(cards, list), f"expected list[ChoiceCard], got {cards!r}"
    return [c.id for c in cards]


def test_cache_stable_default_is_off() -> None:
    """Default behavior is unchanged: no flag, no tracking, no marker."""
    runtime = _make_runtime()
    assert runtime.cache_stable is False
    assert runtime.browsed_tool_ids == frozenset()

    cards_a = runtime.browse(query="open a github issue")
    cards_b = runtime.browse(query="open a github issue")
    # The session never tracks ids when cache_stable is off.
    assert runtime.browsed_tool_ids == frozenset()
    # No marker is emitted.
    assert CACHE_BREAKPOINT_ID not in _ids(cards_a)
    assert CACHE_BREAKPOINT_ID not in _ids(cards_b)
    # Order is the existing score-desc / id-asc ordering — identical across calls.
    assert _ids(cards_a) == _ids(cards_b)


def test_cache_stable_browsed_tool_ids_returns_frozenset() -> None:
    """The session-tracking accessor returns an immutable snapshot."""
    runtime = _make_cache_stable_runtime()
    runtime.browse(query="open a github issue")
    snap = runtime.browsed_tool_ids
    assert isinstance(snap, frozenset)
    # Mutation of the snapshot would corrupt runtime state; frozenset has
    # no ``add`` method, which is exactly the protection we want.
    assert not hasattr(snap, "add")


def test_cache_stable_first_browse_has_no_marker() -> None:
    """Before any ids are recorded, the response has nothing to mark."""
    runtime = _make_cache_stable_runtime()
    cards = runtime.browse(query="open a github issue")
    assert CACHE_BREAKPOINT_ID not in _ids(cards)
    # All emitted ids are recorded for the next browse.
    assert runtime.browsed_tool_ids == frozenset(_ids(cards))


def test_cache_stable_repeated_same_query_has_no_marker_either() -> None:
    """If the second browse returns exactly the same id set, the response is
    all-seen — no new tools, so no marker."""
    runtime = _make_cache_stable_runtime()
    cards1 = runtime.browse(query="open a github issue")
    cards2 = runtime.browse(query="open a github issue")
    # Both runs return cards from the same id set (minus any marker).
    ids1 = set(_ids(cards1))
    ids2 = set(_ids(cards2)) - {CACHE_BREAKPOINT_ID}
    assert ids2 == ids1, f"second browse diverged: missing={ids1 - ids2}, extra={ids2 - ids1}"
    assert CACHE_BREAKPOINT_ID not in _ids(cards2), "all-seen response should not emit a marker"


def test_cache_stable_same_query_response_byte_identical() -> None:
    """The strongest byte-stability guarantee: the same query produces the
    same byte sequence on every call. This is what prompt caches hash on."""
    import json as _json

    runtime = _make_cache_stable_runtime()
    # Two browses of the same query — second call has every id in the seen
    # set, so the response goes entirely through the cache-stable prefix.
    cards_a = runtime.browse(query="open a github issue")
    cards_b = runtime.browse(query="open a github issue")
    assert isinstance(cards_a, list) and isinstance(cards_b, list)
    bytes_a = _json.dumps([c.to_dict() for c in cards_a], sort_keys=True).encode("utf-8")
    bytes_b = _json.dumps([c.to_dict() for c in cards_b], sort_keys=True).encode("utf-8")
    assert bytes_a == bytes_b, "repeated same-query browse is not byte-identical"


def test_cache_stable_overlapping_cards_have_identical_bytes_across_queries() -> None:
    """For ids that appear in two browse responses under different queries,
    the cached frozen content must be byte-identical — this is the
    prompt-cache-hit guarantee for cards in the common prefix."""
    import json as _json

    runtime = _make_cache_stable_runtime()
    cards_a = runtime.browse(query="open a github issue")
    cards_b = runtime.browse(query="post a slack message")
    assert isinstance(cards_a, list) and isinstance(cards_b, list)

    by_id_a = {c.id: c for c in cards_a if c.id != CACHE_BREAKPOINT_ID}
    by_id_b = {c.id: c for c in cards_b if c.id != CACHE_BREAKPOINT_ID}
    overlap = set(by_id_a) & set(by_id_b)
    assert overlap, "test setup needs overlapping tools across queries"
    for cid in overlap:
        # Card content must be byte-identical despite the router having scored
        # the item differently under the two queries — the cache freezes the
        # first-sighting content, including score, so the cache prefix is
        # genuinely stable.
        a_bytes = _json.dumps(by_id_a[cid].to_dict(), sort_keys=True).encode("utf-8")
        b_bytes = _json.dumps(by_id_b[cid].to_dict(), sort_keys=True).encode("utf-8")
        assert a_bytes == b_bytes, f"id={cid!r}: cache-stable card content drifted between browses"


def test_cache_stable_prefix_is_id_asc_for_seen_set() -> None:
    """Once cards are in the seen set, they appear in ascending-id order in
    the prefix — never the score-desc order that the default produces."""
    runtime = _make_cache_stable_runtime()
    # First browse seeds the seen set.
    runtime.browse(query="open a github issue")
    # Second browse with a query that surfaces the same ids — the response is
    # all-seen and must be ascending-id.
    cards = runtime.browse(query="open a github issue")
    ids = [c.id for c in cards if c.id != CACHE_BREAKPOINT_ID]
    assert ids == sorted(ids), f"cache-stable seen prefix is not id-asc: {ids}"


def test_cache_stable_new_tools_appended_after_marker() -> None:
    """Tools that appear in a browse but were not previously seen must land
    AFTER the marker, in ascending-id order."""
    runtime = _make_cache_stable_runtime()
    tool_ids = sorted(runtime.list_tool_ids())
    # Pin one tool as "seen" by hydrating it directly.
    seeded_id = tool_ids[0]
    runtime.hydrate(seeded_id)
    assert runtime.browsed_tool_ids == frozenset({seeded_id})

    cards = runtime.browse(query="open a github issue")
    ids = _ids(cards)
    assert CACHE_BREAKPOINT_ID in ids, "marker missing when both seen and new are present"
    marker_idx = ids.index(CACHE_BREAKPOINT_ID)

    seen_half = ids[:marker_idx]
    new_half = ids[marker_idx + 1 :]
    # Seen half: just the one seeded id, no marker.
    assert seen_half == [seeded_id]
    # New half: all other emitted ids, sorted ascending.
    assert new_half == sorted(new_half), "new-half is not deterministic-by-id-asc"
    # Marker is internal-kind.
    marker = cards[marker_idx]
    assert isinstance(marker, ChoiceCard)
    assert marker.kind == "internal"


def test_cache_stable_marker_only_when_both_sides_present() -> None:
    """Marker is suppressed when seen-half OR new-half is empty (no boundary)."""
    runtime = _make_cache_stable_runtime()
    # First browse → all-new, no marker.
    cards1 = runtime.browse(query="open a github issue")
    assert CACHE_BREAKPOINT_ID not in _ids(cards1)
    # Same browse again → all-seen, no marker.
    cards2 = runtime.browse(query="open a github issue")
    assert CACHE_BREAKPOINT_ID not in _ids(cards2)


def test_cache_stable_hydrate_updates_seen_set() -> None:
    """A successful hydrate() records the id so the next browse surfaces it
    in the byte-stable prefix."""
    runtime = _make_cache_stable_runtime()
    tool_ids = sorted(runtime.list_tool_ids())
    target = tool_ids[2]  # slack
    assert target not in runtime.browsed_tool_ids

    result = runtime.hydrate(target)
    assert not isinstance(result, GatewayError)
    assert target in runtime.browsed_tool_ids

    # A subsequent browse that includes `target` must put it in the prefix.
    cards = runtime.browse(query="post a slack message")
    ids = _ids(cards)
    if target in ids:
        marker_idx = ids.index(CACHE_BREAKPOINT_ID) if CACHE_BREAKPOINT_ID in ids else len(ids)
        assert target in ids[:marker_idx], "hydrated tool did not land in cache-stable prefix"


def test_cache_stable_failed_hydrate_does_not_pollute_seen_set() -> None:
    """An unknown tool_id returns GatewayError and does NOT enter the seen set."""
    runtime = _make_cache_stable_runtime()
    result = runtime.hydrate("does:not:exist")
    assert isinstance(result, GatewayError)
    assert result.code == "HYDRATE_FAILED"
    assert "does:not:exist" not in runtime.browsed_tool_ids


@pytest.mark.asyncio
async def test_cache_stable_execute_updates_seen_set() -> None:
    """A successful execute() records the tool_id in browsed_tool_ids."""
    runtime = _make_cache_stable_runtime()
    tool_ids = sorted(runtime.list_tool_ids())
    # github:close_issue@1.4.0 requires issue_id
    target = tool_ids[0]
    assert target not in runtime.browsed_tool_ids

    result = await runtime.execute(target, {"issue_id": 42})
    assert not isinstance(result, GatewayError), f"execute failed: {result}"
    assert target in runtime.browsed_tool_ids


@pytest.mark.asyncio
async def test_cache_stable_failed_execute_does_not_pollute_seen_set() -> None:
    """A failed execute (unknown tool_id) does NOT enter the seen set."""
    runtime = _make_cache_stable_runtime()
    result = await runtime.execute("does:not:exist", {})
    assert isinstance(result, GatewayError)
    assert "does:not:exist" not in runtime.browsed_tool_ids


def test_cache_stable_preserves_score_metadata() -> None:
    """Reordering must preserve ChoiceCard.score so consumers can re-rank."""
    runtime = _make_cache_stable_runtime()
    tool_ids = sorted(runtime.list_tool_ids())
    runtime.hydrate(tool_ids[0])

    cards = runtime.browse(query="open a github issue")
    assert isinstance(cards, list)
    # At least one of the real (non-marker) cards must carry a score from the
    # router; cache_stable reorders but does not overwrite scoring metadata.
    real_cards = [c for c in cards if c.id != CACHE_BREAKPOINT_ID]
    assert any(c.score is not None for c in real_cards), (
        "ChoiceCard.score lost during cache-stable reordering"
    )


def test_cache_stable_does_not_break_path_browse() -> None:
    """Path-browsing produces the same cards either way, just reordered."""
    runtime = _make_cache_stable_runtime()
    by_path = runtime.browse(path="/")
    assert isinstance(by_path, list)
    # First call: all-new, no marker.
    assert CACHE_BREAKPOINT_ID not in _ids(by_path)
    # Re-browse the same path: all-seen, sorted by id, no marker.
    by_path_again = runtime.browse(path="/")
    assert isinstance(by_path_again, list)
    ids_again = _ids(by_path_again)
    assert CACHE_BREAKPOINT_ID not in ids_again
    assert ids_again == sorted(ids_again), "path-browse seen-prefix not id-asc"


def test_cache_stable_browse_propagates_gateway_errors() -> None:
    """A GatewayError must pass through untouched — no marker injection."""
    runtime = _make_cache_stable_runtime()
    # Pass neither query nor path → ARGS_INVALID.
    err = runtime.browse()
    assert isinstance(err, GatewayError)
    assert err.code == "ARGS_INVALID"

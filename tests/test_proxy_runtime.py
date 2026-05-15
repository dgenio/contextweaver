"""Tests for contextweaver.adapters.proxy_runtime (#29).

Exercises the shared core that both the transparent proxy (#13) and the
two-tool gateway (#28) build on.  ``StubUpstream`` lets us drive every
path without spinning up an MCP server.
"""

from __future__ import annotations

from typing import Any

from contextweaver.adapters.gateway_error import GatewayError
from contextweaver.adapters.mcp_upstream import StubUpstream
from contextweaver.adapters.proxy_runtime import ProxyRuntime
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
    # The runtime persists either a structured artifact or a text:
    # artifact under a deterministic handle when the response has no
    # binaries.  Pick whichever exists.
    handles = list(runtime.context_manager.artifact_store.list_refs())
    assert handles, "execute must store the upstream response somewhere"
    handle = handles[0].handle
    sliced = runtime.view(handle, {"type": "head", "n_chars": 8})
    assert isinstance(sliced, str)


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

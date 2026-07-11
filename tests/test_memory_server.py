"""Tests for the standalone MCP memory server (issue #632)."""

from __future__ import annotations

import json
from typing import Any

from contextweaver.adapters.memory_server import (
    MEMORY_ADD_EPISODE,
    MEMORY_GET_FACTS,
    MEMORY_PUT_FACT,
    MEMORY_SEARCH_EPISODES,
    MEMORY_TOOL_NAMES,
    build_memory_server,
    dispatch_memory_tool,
    make_memory_meta_tools,
)
from contextweaver.store.episodic import InMemoryEpisodicStore
from contextweaver.store.facts import InMemoryFactStore


def _payload(result: dict[str, Any]) -> Any:  # noqa: ANN401 - decoded JSON
    """Decode the JSON body of a CallToolResult dict."""
    (part,) = result["content"]
    return json.loads(part["text"])


def _stores() -> tuple[InMemoryEpisodicStore, InMemoryFactStore]:
    return InMemoryEpisodicStore(), InMemoryFactStore()


async def test_add_then_search_episode() -> None:
    episodic, facts = _stores()
    added = await dispatch_memory_tool(
        episodic, facts, MEMORY_ADD_EPISODE, {"summary": "deployed the billing service"}
    )
    assert not added.get("isError")
    episode_id = _payload(added)["episode_id"]
    assert episode_id.startswith("episode:")

    found = await dispatch_memory_tool(
        episodic, facts, MEMORY_SEARCH_EPISODES, {"query": "billing deploy"}
    )
    hits = _payload(found)
    assert [hit["episode_id"] for hit in hits] == [episode_id]
    assert hits[0]["summary"] == "deployed the billing service"


async def test_add_episode_is_idempotent_by_content() -> None:
    episodic, facts = _stores()
    first = await dispatch_memory_tool(episodic, facts, MEMORY_ADD_EPISODE, {"summary": "same"})
    second = await dispatch_memory_tool(episodic, facts, MEMORY_ADD_EPISODE, {"summary": "same"})
    assert _payload(first)["status"] == "added"
    assert _payload(second)["status"] == "exists"
    assert _payload(first)["episode_id"] == _payload(second)["episode_id"]


async def test_caller_supplied_ids_win() -> None:
    episodic, facts = _stores()
    added = await dispatch_memory_tool(
        episodic, facts, MEMORY_ADD_EPISODE, {"episode_id": "ep-1", "summary": "x"}
    )
    assert _payload(added)["episode_id"] == "ep-1"
    stored = await dispatch_memory_tool(
        episodic, facts, MEMORY_PUT_FACT, {"fact_id": "f-1", "key": "region", "value": "eu"}
    )
    assert _payload(stored)["fact_id"] == "f-1"


async def test_put_and_get_facts_by_key() -> None:
    episodic, facts = _stores()
    await dispatch_memory_tool(episodic, facts, MEMORY_PUT_FACT, {"key": "region", "value": "eu"})
    await dispatch_memory_tool(episodic, facts, MEMORY_PUT_FACT, {"key": "tier", "value": "prod"})
    by_key = _payload(
        await dispatch_memory_tool(episodic, facts, MEMORY_GET_FACTS, {"key": "region"})
    )
    assert [fact["value"] for fact in by_key] == ["eu"]
    all_facts = _payload(await dispatch_memory_tool(episodic, facts, MEMORY_GET_FACTS, {}))
    assert {fact["key"] for fact in all_facts} == {"region", "tier"}


async def test_invalid_args_return_gateway_error_not_crash() -> None:
    episodic, facts = _stores()
    cases: list[tuple[str, dict[str, Any]]] = [
        (MEMORY_ADD_EPISODE, {}),
        (MEMORY_ADD_EPISODE, {"summary": "   "}),
        (MEMORY_ADD_EPISODE, {"summary": "ok", "metadata": "not-an-object"}),
        (MEMORY_SEARCH_EPISODES, {"query": ""}),
        (MEMORY_SEARCH_EPISODES, {"query": "x", "limit": 0}),
        (MEMORY_SEARCH_EPISODES, {"query": "x", "limit": True}),
        (MEMORY_PUT_FACT, {"key": "k"}),
        (MEMORY_GET_FACTS, {"key": 7}),
        ("unknown_tool", {}),
    ]
    for name, args in cases:
        result = await dispatch_memory_tool(episodic, facts, name, args)
        assert result["isError"] is True, (name, args)
        assert _payload(result)["error"] == "ARGS_INVALID"


async def test_redact_secrets_scrubs_read_paths() -> None:
    episodic, facts = _stores()
    secret = "api_key=sk-1234567890abcdef1234567890abcdef"
    await dispatch_memory_tool(
        episodic, facts, MEMORY_ADD_EPISODE, {"summary": f"rotated {secret} today"}
    )
    await dispatch_memory_tool(episodic, facts, MEMORY_PUT_FACT, {"key": "cred", "value": secret})

    hits = _payload(
        await dispatch_memory_tool(
            episodic, facts, MEMORY_SEARCH_EPISODES, {"query": "rotated"}, redact_secrets=True
        )
    )
    assert "sk-1234567890abcdef1234567890abcdef" not in hits[0]["summary"]

    got = _payload(
        await dispatch_memory_tool(
            episodic, facts, MEMORY_GET_FACTS, {"key": "cred"}, redact_secrets=True
        )
    )
    assert "sk-1234567890abcdef1234567890abcdef" not in got[0]["value"]

    # Default (no redaction) returns memory verbatim — operator's choice.
    raw = _payload(await dispatch_memory_tool(episodic, facts, MEMORY_GET_FACTS, {"key": "cred"}))
    assert raw[0]["value"] == secret


def test_meta_tool_definitions_shape() -> None:
    tools = make_memory_meta_tools()
    assert [tool["name"] for tool in tools] == list(MEMORY_TOOL_NAMES)
    for tool in tools:
        schema = tool["inputSchema"]
        assert schema["type"] == "object"
        assert schema["additionalProperties"] is False
        assert tool["description"]


def test_build_memory_server_constructs() -> None:
    episodic, facts = _stores()
    server = build_memory_server(episodic, facts, server_name="test-memory")
    assert server.name == "test-memory"

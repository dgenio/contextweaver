"""Tests for the concrete resource/prompt upstream adapters (#669 / #670)."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

import pytest

from contextweaver.adapters.gateway_primitives import PrimitiveGatewayRuntime, PrimitiveUpstream
from contextweaver.adapters.mcp_primitive_upstream import (
    McpClientPrimitiveUpstream,
    MultiplexPrimitiveUpstream,
    StubPrimitiveUpstream,
)
from contextweaver.envelope import ResultEnvelope

RESOURCES = [
    {"uri": "file:///docs/readme.md", "name": "README", "mimeType": "text/markdown"},
    {"uri": "postgres://db/users", "name": "users", "description": "user records"},
]
PROMPTS = [
    {"name": "greet", "description": "Greet a user", "arguments": [{"name": "who"}]},
]


# --- StubPrimitiveUpstream ---------------------------------------------------


def test_stub_satisfies_protocol() -> None:
    assert isinstance(StubPrimitiveUpstream(), PrimitiveUpstream)


async def test_stub_lists_copies_not_references() -> None:
    stub = StubPrimitiveUpstream(RESOURCES, PROMPTS)
    listed = await stub.list_resources()
    listed[0]["uri"] = "mutated"
    # Mutating the returned copy must not corrupt the stub's backing defs.
    assert (await stub.list_resources())[0]["uri"] == "file:///docs/readme.md"
    assert len(await stub.list_prompts()) == 1


async def test_stub_canned_read_and_get() -> None:
    stub = StubPrimitiveUpstream(RESOURCES, PROMPTS)
    read = await stub.read_resource("file:///docs/readme.md")
    assert read["contents"][0]["uri"] == "file:///docs/readme.md"
    assert read["contents"][0]["text"]
    got = await stub.get_prompt("greet", {"who": "ada"})
    assert got["messages"][0]["role"] == "user"
    assert "greet" in got["messages"][0]["content"]["text"]


async def test_stub_handlers_override_canned() -> None:
    async def read_handler(uri: str) -> dict[str, Any]:
        return {"contents": [{"uri": uri, "mimeType": "text/plain", "text": "custom"}]}

    async def get_handler(name: str, args: dict[str, Any]) -> dict[str, Any]:
        return {"description": name, "messages": [{"role": "assistant", "content": "custom"}]}

    stub = StubPrimitiveUpstream(read_handler=read_handler, get_handler=get_handler)
    assert (await stub.read_resource("x"))["contents"][0]["text"] == "custom"
    assert (await stub.get_prompt("p", {}))["messages"][0]["content"] == "custom"


async def test_stub_drives_runtime_end_to_end() -> None:
    """The shipped stub feeds the runtime exactly like the in-test stub does."""
    rt = PrimitiveGatewayRuntime(StubPrimitiveUpstream(RESOURCES, PROMPTS))
    assert await rt.refresh() == (2, 1)
    cards = rt.browse_resources(query="readme documentation")
    assert isinstance(cards, list) and cards
    envelope = await rt.read_resource(cards[0].id)
    assert isinstance(envelope, ResultEnvelope) and envelope.status == "ok"


# --- McpClientPrimitiveUpstream ----------------------------------------------


class _FakeSession:
    """Minimal stand-in for an MCP ClientSession (model_dump-shaped results)."""

    def __init__(self, *, sleep: float = 0.0) -> None:
        self._sleep = sleep

    async def list_resources(self) -> object:
        return type("R", (), {"resources": [dict(r) for r in RESOURCES]})()

    async def read_resource(self, uri: str) -> dict[str, Any]:
        if self._sleep:
            await asyncio.sleep(self._sleep)
        return {"contents": [{"uri": uri, "mimeType": "text/plain", "text": "body"}]}

    async def list_prompts(self) -> object:
        return type("P", (), {"prompts": [dict(p) for p in PROMPTS]})()

    async def get_prompt(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {"description": name, "messages": []}


async def test_client_unwraps_listings_to_dicts() -> None:
    client = McpClientPrimitiveUpstream(_FakeSession())
    resources = await client.list_resources()
    assert resources[0]["uri"] == "file:///docs/readme.md"
    prompts = await client.list_prompts()
    assert prompts[0]["name"] == "greet"


async def test_client_read_and_get_return_dicts() -> None:
    client = McpClientPrimitiveUpstream(_FakeSession())
    read = await client.read_resource("file:///x")
    assert read["contents"][0]["uri"] == "file:///x"
    assert (await client.get_prompt("greet", {}))["description"] == "greet"


async def test_client_propagates_timeout_for_runtime_to_classify() -> None:
    """Per the PrimitiveUpstream contract, transport timeouts raise (not swallowed)."""
    client = McpClientPrimitiveUpstream(_FakeSession(sleep=0.05), timeout=0.01)
    with pytest.raises((TimeoutError, asyncio.TimeoutError)):
        await client.read_resource("file:///slow")


# --- MultiplexPrimitiveUpstream ----------------------------------------------


async def test_multiplex_unions_and_routes_to_owner() -> None:
    a = StubPrimitiveUpstream(
        [{"uri": "file:///a", "name": "a"}],
        [{"name": "alpha"}],
        read_handler=_tagged_read("from-a"),
        get_handler=_tagged_get("from-a"),
    )
    b = StubPrimitiveUpstream(
        [{"uri": "file:///b", "name": "b"}],
        [{"name": "beta"}],
        read_handler=_tagged_read("from-b"),
        get_handler=_tagged_get("from-b"),
    )
    mux = MultiplexPrimitiveUpstream([a, b])
    assert {r["uri"] for r in await mux.list_resources()} == {"file:///a", "file:///b"}
    assert {p["name"] for p in await mux.list_prompts()} == {"alpha", "beta"}
    assert (await mux.read_resource("file:///b"))["contents"][0]["text"] == "from-b"
    assert (await mux.get_prompt("alpha", {}))["messages"][0]["content"] == "from-a"


async def test_multiplex_first_source_wins_on_collision() -> None:
    a = StubPrimitiveUpstream([{"uri": "file:///x", "name": "a"}], read_handler=_tagged_read("a"))
    b = StubPrimitiveUpstream([{"uri": "file:///x", "name": "b"}], read_handler=_tagged_read("b"))
    mux = MultiplexPrimitiveUpstream([a, b])
    union = await mux.list_resources()
    assert len(union) == 1
    assert (await mux.read_resource("file:///x"))["contents"][0]["text"] == "a"


async def test_multiplex_unknown_id_raises_for_classification() -> None:
    mux = MultiplexPrimitiveUpstream([StubPrimitiveUpstream([{"uri": "file:///a"}], [])])
    await mux.list_resources()
    with pytest.raises(LookupError):
        await mux.read_resource("file:///missing")
    with pytest.raises(LookupError):
        await mux.get_prompt("missing", {})


def _tagged_read(text: str) -> Callable[[str], Awaitable[dict[str, Any]]]:
    async def handler(uri: str) -> dict[str, Any]:
        return {"contents": [{"uri": uri, "mimeType": "text/plain", "text": text}]}

    return handler


def _tagged_get(text: str) -> Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]:
    async def handler(name: str, args: dict[str, Any]) -> dict[str, Any]:
        return {"description": name, "messages": [{"role": "user", "content": text}]}

    return handler

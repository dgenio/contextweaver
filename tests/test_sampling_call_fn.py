"""Tests for the MCP sampling-backed call_fn (issue #623)."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from mcp import types as mcp_types

from contextweaver.adapters.sampling_call_fn import (
    make_async_sampling_call_fn,
    make_sampling_call_fn,
)
from contextweaver.exceptions import ConfigError


class _FakeSession:
    """Stands in for ServerSession; records create_message kwargs."""

    def __init__(self, content: Any) -> None:  # noqa: ANN401 - SDK content union
        self._content = content
        self.kwargs: dict[str, Any] = {}

    async def create_message(self, **kwargs: Any) -> mcp_types.CreateMessageResult:  # noqa: ANN401
        self.kwargs = kwargs
        return mcp_types.CreateMessageResult(
            role="assistant", content=self._content, model="client-model-1"
        )


def _text_session(text: str = "a compact summary") -> _FakeSession:
    return _FakeSession(mcp_types.TextContent(type="text", text=text))


async def test_async_call_fn_returns_text_and_shapes_request() -> None:
    session = _text_session()
    prefs = mcp_types.ModelPreferences(intelligencePriority=0.2, speedPriority=0.9)
    call_fn = make_async_sampling_call_fn(lambda: session, model_preferences=prefs, max_tokens=99)
    assert await call_fn("summarize this") == "a compact summary"
    (message,) = session.kwargs["messages"]
    assert isinstance(message, mcp_types.SamplingMessage)
    assert message.role == "user" and message.content.text == "summarize this"
    assert session.kwargs["max_tokens"] == 99
    assert session.kwargs["model_preferences"] is prefs


async def test_async_non_text_content_raises_config_error() -> None:
    session = _FakeSession(mcp_types.ImageContent(type="image", data="aGk=", mimeType="image/png"))
    call_fn = make_async_sampling_call_fn(lambda: session)
    with pytest.raises(ConfigError, match="non-text"):
        await call_fn("summarize")


async def test_async_missing_session_raises() -> None:
    call_fn = make_async_sampling_call_fn(lambda: None)
    with pytest.raises(ConfigError, match="no connected client"):
        await call_fn("x")


async def test_sync_call_fn_works_from_worker_thread() -> None:
    session = _text_session("threaded ok")
    call_fn = make_sampling_call_fn(lambda: session, timeout_seconds=5.0)
    result = await asyncio.to_thread(call_fn, "summarize")
    assert result == "threaded ok"


async def test_sync_call_fn_rejects_loop_thread_invocation() -> None:
    call_fn = make_sampling_call_fn(lambda: _text_session(), timeout_seconds=5.0)
    with pytest.raises(ConfigError, match="deadlock"):
        call_fn("summarize")


def test_sync_construction_outside_loop_requires_explicit_loop() -> None:
    with pytest.raises(ConfigError, match="event loop"):
        make_sampling_call_fn(lambda: _text_session())


async def test_sync_timeout_becomes_config_error() -> None:
    class _SlowSession:
        async def create_message(self, **kwargs: Any) -> mcp_types.CreateMessageResult:  # noqa: ANN401
            await asyncio.sleep(30)
            raise AssertionError("unreachable")

    call_fn = make_sampling_call_fn(lambda: _SlowSession(), timeout_seconds=0.05)
    with pytest.raises(ConfigError, match="exceeded"):
        await asyncio.to_thread(call_fn, "x")

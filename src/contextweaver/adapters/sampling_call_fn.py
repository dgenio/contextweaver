"""MCP sampling-backed ``call_fn`` for gateway deployments (issue #623).

The firewall's optional LLM summarizer (issues #26/#384) takes a
caller-supplied ``call_fn: (prompt) -> completion``.  In a gateway
deployment there is often no separate model credential — but the *connected
MCP client* usually has one, and the MCP spec exposes it to servers through
``sampling/createMessage``.  This module bridges the two: it builds a
``call_fn`` that forwards the prompt to the client's own model via the
server session's :meth:`~mcp.server.session.ServerSession.create_message`.

Opt-in only: nothing imports this module unless the operator wires it, and
the client must have granted the sampling capability — a client without it
fails the call, which every consumer (``LlmSummarizer`` etc.) already treats
as "fall back to the deterministic path".

Sync/async bridging: the ``Summarizer`` protocol is synchronous while
sampling is an async server→client RPC.  :func:`make_sampling_call_fn`
therefore returns a *sync* callable that submits the coroutine to the
server's running event loop via :func:`asyncio.run_coroutine_threadsafe` —
valid **only from a worker thread**.  Calling it on the event-loop thread
itself would deadlock (the loop cannot both block on and serve the RPC), so
that case raises :class:`~contextweaver.exceptions.ConfigError` immediately.
Async call sites should use :func:`make_async_sampling_call_fn` instead.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from contextweaver.exceptions import ConfigError

if TYPE_CHECKING:
    from mcp import types as mcp_types

#: Zero-arg provider of the active server session (or any object exposing
#: ``create_message``).  A provider indirection is used because the session
#: only exists while a client is connected.
SessionProvider = Callable[[], Any]


def _build_request_kwargs(
    prompt: str,
    max_tokens: int,
    model_preferences: mcp_types.ModelPreferences | None,
) -> dict[str, Any]:
    """Assemble the ``create_message`` keyword arguments for *prompt*."""
    from mcp import types as mcp_types_runtime

    message = mcp_types_runtime.SamplingMessage(
        role="user",
        content=mcp_types_runtime.TextContent(type="text", text=prompt),
    )
    kwargs: dict[str, Any] = {"messages": [message], "max_tokens": max_tokens}
    if model_preferences is not None:
        kwargs["model_preferences"] = model_preferences
    return kwargs


def _extract_text(result: Any) -> str:  # noqa: ANN401 - SDK result union
    """Return the text completion from a ``CreateMessageResult``.

    Raises:
        ConfigError: When the client returned non-text content — the caller's
            deterministic fallback should take over.
    """
    content = getattr(result, "content", None)
    text = getattr(content, "text", None)
    if getattr(content, "type", None) == "text" and isinstance(text, str):
        return text
    kind = getattr(content, "type", type(content).__name__)
    raise ConfigError(
        f"sampling returned non-text content ({kind!r}); "
        "falling back to the deterministic summarizer",
        hint="the connected client's sampling handler must return text content",
    )


def make_async_sampling_call_fn(
    session_provider: SessionProvider,
    *,
    model_preferences: mcp_types.ModelPreferences | None = None,
    max_tokens: int = 512,
) -> Callable[[str], Coroutine[Any, Any, str]]:
    """Return an async ``call_fn`` sampling through the connected client.

    Args:
        session_provider: Zero-arg callable returning the active
            :class:`~mcp.server.session.ServerSession` (raise or return
            ``None`` when no client is connected — both become errors here).
        model_preferences: Optional MCP model-selection hints.
        max_tokens: Completion budget requested from the client.

    Returns:
        ``async (prompt) -> completion text``.
    """

    async def call_fn(prompt: str) -> str:
        session = session_provider()
        if session is None:
            raise ConfigError("no connected client session available for sampling")
        result = await session.create_message(
            **_build_request_kwargs(prompt, max_tokens, model_preferences)
        )
        return _extract_text(result)

    return call_fn


def make_sampling_call_fn(
    session_provider: SessionProvider,
    *,
    model_preferences: mcp_types.ModelPreferences | None = None,
    max_tokens: int = 512,
    timeout_seconds: float = 30.0,
    loop: asyncio.AbstractEventLoop | None = None,
) -> Callable[[str], str]:
    """Return a sync ``call_fn`` sampling through the connected client.

    The returned callable is intended for the synchronous ``Summarizer``
    seam.  It must run on a **worker thread** (e.g. the thread
    ``ContextManager`` offloads builds to): it captures the server's event
    loop and blocks on :func:`asyncio.run_coroutine_threadsafe`.

    Args:
        session_provider: Zero-arg callable returning the active session.
        model_preferences: Optional MCP model-selection hints.
        max_tokens: Completion budget requested from the client.
        timeout_seconds: Hard wall-clock bound on the round-trip.
        loop: The server's event loop.  Defaults to the running loop at
            construction time — construct the ``call_fn`` from async serving
            code (where the loop is running) and hand it to worker-side
            consumers.

    Returns:
        ``(prompt) -> completion text``.

    Raises:
        ConfigError: At construction when no loop is given and none is
            running; at call time when invoked *on* the loop thread (which
            would deadlock) or when the round-trip times out.
    """
    if loop is None:
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            raise ConfigError(
                "make_sampling_call_fn requires the server event loop",
                hint="construct it from async serving code, or pass loop= explicitly",
            ) from None
    async_call = make_async_sampling_call_fn(
        session_provider, model_preferences=model_preferences, max_tokens=max_tokens
    )

    def call_fn(prompt: str) -> str:
        running: asyncio.AbstractEventLoop | None
        try:
            running = asyncio.get_running_loop()
        except RuntimeError:
            running = None
        if running is loop:
            raise ConfigError(
                "sampling call_fn invoked on the event-loop thread; this would deadlock",
                hint="run the summarizer on a worker thread or use make_async_sampling_call_fn",
            )
        future: concurrent.futures.Future[str] = asyncio.run_coroutine_threadsafe(
            async_call(prompt), loop
        )
        try:
            return future.result(timeout=timeout_seconds)
        except TimeoutError:
            future.cancel()
            raise ConfigError(
                f"sampling round-trip exceeded {timeout_seconds}s",
                hint="raise timeout_seconds or check the client's sampling handler",
            ) from None

    return call_fn


__all__ = [
    "SessionProvider",
    "make_async_sampling_call_fn",
    "make_sampling_call_fn",
]

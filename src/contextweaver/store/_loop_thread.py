"""Private event-loop thread for the async-to-sync store bridges (issue #495).

Extracted from :mod:`contextweaver.store._async_to_sync` to keep that module
within the 300-line ceiling. Holds :class:`_LoopThread` and its timeout
constants (issue #750). Not public API; re-exported from ``_async_to_sync`` so
existing ``from ..._async_to_sync import _LoopThread`` imports keep working.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import threading
from collections.abc import Coroutine
from typing import Any, TypeVar

from contextweaver.exceptions import StoreTimeoutError

_T = TypeVar("_T")

#: Default per-operation timeout (seconds) for the sync store bridge.  Generous
#: enough for a slow-but-healthy network backend call (redis/S3/Zep), while
#: still bounding a hung backend so it cannot wedge the loop thread forever
#: (issue #750).  Override per-manager via ``_LoopThread(timeout=...)`` or
#: per-call via ``run(coro, timeout=...)``; ``timeout=None`` waits indefinitely.
_DEFAULT_STORE_OP_TIMEOUT = 30.0

#: Sentinel distinguishing "caller did not pass timeout" (use the instance
#: default) from an explicit ``timeout=None`` (wait forever).
_USE_DEFAULT_TIMEOUT: Any = object()


class _LoopThread:
    """A private asyncio event loop running in its own daemon thread.

    Shared by all async-to-sync bridges attached to one
    :class:`~contextweaver.context.manager.ContextManager`, so async store I/O
    runs off the caller's loop.  :meth:`run` submits a coroutine and blocks the
    calling thread until it completes.

    Args:
        timeout: Default per-operation timeout in seconds applied by
            :meth:`run` when the caller does not override it.  ``None`` waits
            indefinitely (the pre-#750 behaviour).
    """

    def __init__(self, timeout: float | None = _DEFAULT_STORE_OP_TIMEOUT) -> None:
        self._loop = asyncio.new_event_loop()
        self._timeout = timeout
        self._thread = threading.Thread(target=self._serve, name="cw-store-loop", daemon=True)
        self._thread.start()

    def _serve(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def run(
        self, coro: Coroutine[Any, Any, _T], timeout: float | None = _USE_DEFAULT_TIMEOUT
    ) -> _T:
        """Run *coro* on the private loop and return its result (blocking).

        Blocks the calling thread until the coroutine completes or *timeout*
        seconds elapse.  On timeout the pending coroutine is cancelled and
        :class:`~contextweaver.exceptions.StoreTimeoutError` is raised rather
        than hanging indefinitely (issue #750) — a single stuck backend call
        would otherwise wedge this loop thread and, via the manager build lock,
        every subsequent ``build()``.

        Args:
            coro: The coroutine to drive on the private loop.
            timeout: Seconds to wait; defaults to the instance timeout.
                ``None`` waits forever.
        """
        effective = self._timeout if timeout is _USE_DEFAULT_TIMEOUT else timeout
        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        try:
            return future.result(timeout=effective)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise StoreTimeoutError(
                f"async store operation did not complete within {effective}s"
            ) from None

    def close(self) -> None:
        """Stop the private loop and join its thread.  Idempotent.

        Cancels and drains any still-pending tasks first — e.g. a coroutine
        abandoned by a :meth:`run` that timed out (issue #750) — so the loop
        stops cleanly without a ``Task was destroyed but it is pending``
        warning to stderr.
        """
        if self._loop.is_closed():
            return

        async def _drain() -> None:
            pending = [t for t in asyncio.all_tasks() if t is not asyncio.current_task()]
            for task in pending:
                task.cancel()
            for task in pending:
                with contextlib.suppress(BaseException):
                    await task

        with contextlib.suppress(concurrent.futures.TimeoutError, RuntimeError):
            asyncio.run_coroutine_threadsafe(_drain(), self._loop).result(timeout=5)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._thread.join(timeout=5)
        self._loop.close()

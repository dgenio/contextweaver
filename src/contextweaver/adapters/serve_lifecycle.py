"""Graceful shutdown for the gateway serve loops (issue #626).

Transport-agnostic shutdown orchestration for ``mcp serve``: the same
controller works for stdio (client EOF), SSE, and streamable-HTTP serving.
It owns three concerns:

1. **Signal capture** — :meth:`ShutdownController.install_signal_handlers`
   registers ``SIGINT`` / ``SIGTERM`` on the running asyncio loop and flips
   :attr:`ShutdownController.requested`.  Platforms/loops without
   ``loop.add_signal_handler`` (e.g. Windows Proactor) degrade gracefully:
   the failure is recorded on
   :attr:`ShutdownReport.signal_handlers_installed` instead of crashing.
2. **Drain** — :meth:`ShutdownController.drain` awaits in-flight work up to a
   timeout, then cancels the stragglers, counting both.
3. **Flush** — :meth:`ShutdownController.flush` defensively calls ``flush()``
   then ``close()`` on stores/sinks, collecting per-object errors into the
   report rather than raising mid-shutdown.

Intended CLI wiring (the coordinator owns ``_mcp_cli``):

- ``_serve_*`` builds a ``ShutdownController`` before entering its serve
  loop and calls ``install_signal_handlers()`` once the loop is running.
- The serve coroutine races against ``controller.requested.wait()`` (e.g.
  via ``asyncio.wait(..., return_when=FIRST_COMPLETED)``); whichever side
  finishes first (client EOF or a signal) starts the shutdown sequence.
- On shutdown: ``await controller.drain(pending_tasks, timeout)`` then
  ``await controller.flush([event_log, artifact_store, diagnostic_sink])``,
  log ``controller.report.to_dict()``, and finally
  ``controller.uninstall_signal_handlers()``.
"""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import logging
import signal
from collections.abc import Awaitable, Iterable
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("contextweaver.adapters.serve_lifecycle")

#: Signals a controller captures by default.
DEFAULT_SHUTDOWN_SIGNALS: tuple[signal.Signals, ...] = (signal.SIGINT, signal.SIGTERM)

#: Method names :meth:`ShutdownController.flush` tries on each closeable,
#: in order.  ``flush`` persists buffered state before ``close`` releases
#: the underlying resource.
_FLUSH_METHOD_NAMES: tuple[str, ...] = ("flush", "close")


@dataclass
class ShutdownReport:
    """Outcome of one graceful-shutdown sequence (issue #626).

    Attributes:
        drained: In-flight awaitables that completed within the drain timeout.
        cancelled: In-flight awaitables cancelled after the drain timeout.
        flush_errors: One human-readable entry per closeable whose
            ``flush()`` / ``close()`` raised; empty on a clean shutdown.
        signal_handlers_installed: Whether ``SIGINT`` / ``SIGTERM`` handlers
            were actually registered on the loop (``False`` on platforms or
            loops without ``loop.add_signal_handler`` support).
    """

    drained: int = 0
    cancelled: int = 0
    flush_errors: list[str] = field(default_factory=list)
    signal_handlers_installed: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "drained": self.drained,
            "cancelled": self.cancelled,
            "flush_errors": list(self.flush_errors),
            "signal_handlers_installed": self.signal_handlers_installed,
        }


class ShutdownController:
    """Coordinates signal capture, drain, and flush for one serve loop.

    The controller is idempotent throughout: :meth:`request` may fire any
    number of times (signal + EOF racing is fine), installing handlers twice
    is a no-op, and :meth:`drain` / :meth:`flush` accumulate into the same
    :attr:`report`.

    Args:
        signals: Signals to capture.  Defaults to
            :data:`DEFAULT_SHUTDOWN_SIGNALS` (``SIGINT`` + ``SIGTERM``).
    """

    def __init__(self, *, signals: tuple[signal.Signals, ...] = DEFAULT_SHUTDOWN_SIGNALS) -> None:
        self._signals = signals
        #: Set once shutdown has been requested (signal, EOF, or explicit call).
        self.requested: asyncio.Event = asyncio.Event()
        #: Accumulated outcome of this shutdown sequence.
        self.report: ShutdownReport = ShutdownReport()
        self._installed_signals: list[signal.Signals] = []

    def request(self) -> None:
        """Mark shutdown as requested.  Safe to call repeatedly."""
        if not self.requested.is_set():
            logger.info("serve_lifecycle: shutdown requested")
        self.requested.set()

    def install_signal_handlers(self, loop: asyncio.AbstractEventLoop | None = None) -> bool:
        """Register the configured signal handlers on *loop*.

        Falls back gracefully where ``loop.add_signal_handler`` is unsupported
        (non-POSIX platforms, some loop implementations): the failure is
        recorded on :attr:`ShutdownReport.signal_handlers_installed` and the
        caller can rely on transport EOF / ``KeyboardInterrupt`` instead.

        Args:
            loop: Target loop.  Defaults to the running loop.

        Returns:
            ``True`` if every configured handler was registered.
        """
        if self._installed_signals:
            return self.report.signal_handlers_installed
        loop = loop or asyncio.get_running_loop()
        try:
            for sig in self._signals:
                loop.add_signal_handler(sig, self.request)
                self._installed_signals.append(sig)
        except (NotImplementedError, RuntimeError, ValueError) as exc:
            logger.warning(
                "serve_lifecycle: signal handlers unavailable on this platform/loop (%r); "
                "relying on transport EOF for shutdown",
                exc,
            )
            self._uninstall(loop)
            self.report.signal_handlers_installed = False
            return False
        self.report.signal_handlers_installed = True
        return True

    def uninstall_signal_handlers(self, loop: asyncio.AbstractEventLoop | None = None) -> None:
        """Remove any handlers registered by :meth:`install_signal_handlers`."""
        self._uninstall(loop or asyncio.get_running_loop())

    def _uninstall(self, loop: asyncio.AbstractEventLoop) -> None:
        for sig in self._installed_signals:
            with contextlib.suppress(NotImplementedError, RuntimeError, ValueError):
                loop.remove_signal_handler(sig)
        self._installed_signals.clear()

    async def drain(self, pending: Iterable[Awaitable[object]], timeout: float) -> ShutdownReport:
        """Await in-flight work up to *timeout* seconds, then cancel the rest.

        Every awaitable is scheduled as a task (already-running tasks pass
        through unchanged), awaited together under the shared *timeout*, and
        whatever has not finished is cancelled and awaited to completion so no
        task outlives the serve loop.  Results and exceptions are consumed —
        a failing task counts as drained (it *finished*), matching the
        shutdown goal of quiescence rather than success.

        Args:
            pending: In-flight awaitables (tasks, futures, or coroutines),
                processed in iteration order for deterministic accounting.
            timeout: Seconds to wait before cancelling stragglers.

        Returns:
            The controller's :attr:`report`, with :attr:`ShutdownReport.drained`
            and :attr:`ShutdownReport.cancelled` incremented.
        """
        tasks = [asyncio.ensure_future(item) for item in pending]
        if not tasks:
            return self.report
        done, still_pending = await asyncio.wait(tasks, timeout=timeout)
        for task in still_pending:
            task.cancel()
        if still_pending:
            await asyncio.gather(*still_pending, return_exceptions=True)
        # Count in the caller's original order (asyncio.wait returns sets).
        for task in tasks:
            if task in done:
                self.report.drained += 1
                exc = task.exception() if not task.cancelled() else None
                if exc is not None:
                    logger.warning("serve_lifecycle: drained task failed: %r", exc)
            else:
                self.report.cancelled += 1
        return self.report

    async def flush(self, closeables: Iterable[object]) -> ShutdownReport:
        """Flush and close *closeables* defensively, collecting errors.

        For each object, in iteration order, calls ``flush()`` then ``close()``
        when present and callable (awaiting coroutine results).  Errors are
        appended to :attr:`ShutdownReport.flush_errors` instead of raised, so
        one broken sink cannot prevent the remaining stores from closing.

        Args:
            closeables: Stores / sinks / streams to release.

        Returns:
            The controller's accumulated :attr:`report`.
        """
        for obj in closeables:
            for method_name in _FLUSH_METHOD_NAMES:
                method = getattr(obj, method_name, None)
                if not callable(method):
                    continue
                try:
                    result = method()
                    if inspect.isawaitable(result):
                        await result
                except Exception as exc:
                    entry = f"{type(obj).__name__}.{method_name}: {exc}"
                    self.report.flush_errors.append(entry)
                    logger.warning("serve_lifecycle: %s", entry)
        return self.report


__all__ = [
    "DEFAULT_SHUTDOWN_SIGNALS",
    "ShutdownController",
    "ShutdownReport",
]

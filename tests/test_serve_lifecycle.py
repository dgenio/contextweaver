"""Tests for graceful serve shutdown (issue #626)."""

from __future__ import annotations

import asyncio
import os
import signal
import sys

import pytest

from contextweaver.adapters.serve_lifecycle import ShutdownController, ShutdownReport


async def test_drain_counts_fast_and_cancels_slow() -> None:
    controller = ShutdownController()

    async def fast() -> str:
        return "done"

    async def slow() -> str:
        await asyncio.sleep(30)
        return "never"

    report = await controller.drain([fast(), fast(), slow()], timeout=0.1)
    assert report.drained == 2
    assert report.cancelled == 1


async def test_drain_failing_task_counts_as_drained() -> None:
    controller = ShutdownController()

    async def boom() -> None:
        raise RuntimeError("in-flight failure")

    report = await controller.drain([boom()], timeout=0.5)
    assert report.drained == 1
    assert report.cancelled == 0


async def test_drain_empty_is_noop() -> None:
    controller = ShutdownController()
    report = await controller.drain([], timeout=0.1)
    assert report.drained == 0 and report.cancelled == 0


async def test_flush_collects_errors_without_raising() -> None:
    controller = ShutdownController()
    order: list[str] = []

    class Good:
        def flush(self) -> None:
            order.append("good.flush")

        def close(self) -> None:
            order.append("good.close")

    class Broken:
        def close(self) -> None:
            raise OSError("disk gone")

    class AsyncCloseable:
        async def close(self) -> None:
            order.append("async.close")

    report = await controller.flush([Good(), Broken(), AsyncCloseable()])
    assert order == ["good.flush", "good.close", "async.close"]
    assert len(report.flush_errors) == 1
    assert "Broken.close" in report.flush_errors[0]


async def test_request_is_idempotent() -> None:
    controller = ShutdownController()
    assert not controller.requested.is_set()
    controller.request()
    controller.request()
    assert controller.requested.is_set()


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal delivery")
async def test_signal_handler_sets_event() -> None:
    controller = ShutdownController(signals=(signal.SIGTERM,))
    installed = controller.install_signal_handlers()
    assert installed is True
    assert controller.report.signal_handlers_installed is True
    try:
        os.kill(os.getpid(), signal.SIGTERM)
        await asyncio.wait_for(controller.requested.wait(), timeout=2.0)
    finally:
        controller.uninstall_signal_handlers()
    assert controller.requested.is_set()


async def test_install_twice_is_noop() -> None:
    controller = ShutdownController(signals=(signal.SIGTERM,))
    try:
        first = controller.install_signal_handlers()
        second = controller.install_signal_handlers()
        assert first == second
    finally:
        controller.uninstall_signal_handlers()


def test_report_serde() -> None:
    report = ShutdownReport(drained=2, cancelled=1, flush_errors=["X.close: boom"])
    payload = report.to_dict()
    assert payload == {
        "drained": 2,
        "cancelled": 1,
        "flush_errors": ["X.close: boom"],
        "signal_handlers_installed": False,
    }

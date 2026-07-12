"""Concurrency tests for the store layer (issue #458).

These exercise the *documented* guarantees from the thread-safety contract in
``docs/agent-context/architecture.md``: within one process, ``put`` / ``delete``
/ ``list_refs`` on a single ``JsonFileArtifactStore`` instance are serialised by
an internal lock (so concurrent threads sharing it are safe), file writes are
atomic (a reader never sees a torn artifact), and concurrent reads of distinct
handles are safe. The gateway's read-only ``tool_view`` inherits the store
contract. Every test is bounded by iteration count (never wall-clock), so they
are deterministic and non-flaky.
"""

from __future__ import annotations

import asyncio
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from contextweaver import ContextManager
from contextweaver.adapters.mcp_upstream import StubUpstream
from contextweaver.adapters.proxy_runtime import ProxyRuntime
from contextweaver.store import _json_file_io
from contextweaver.store.json_file_artifacts import JsonFileArtifactStore


def test_concurrent_distinct_puts_all_land(tmp_path: Path) -> None:
    """Concurrent writes to distinct handles each land with intact bytes."""
    store = JsonFileArtifactStore(tmp_path)
    handles = [f"h{i}" for i in range(50)]

    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda h: store.put(h, h.encode()), handles))

    for h in handles:
        assert store.get(h) == h.encode()
    assert {r.handle for r in store.list_refs()} == set(handles)


def test_atomic_write_retries_on_permission_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``atomic_write`` retries the swap on PermissionError, then succeeds (#749).

    On Windows ``os.replace`` raises ``PermissionError`` (WinError 5) when a
    concurrent reader holds the destination open. This is unobservable on
    POSIX, so we simulate it: the first two ``os.replace`` calls raise, the
    third succeeds — the write must land intact rather than propagate the
    transient error.
    """
    real_replace = os.replace
    calls = {"n": 0}

    def flaky_replace(src: str, dst: str) -> None:
        calls["n"] += 1
        if calls["n"] < 3:
            raise PermissionError(13, "simulated WinError 5: file in use")
        real_replace(src, dst)

    monkeypatch.setattr(_json_file_io.os, "replace", flaky_replace)
    monkeypatch.setattr(_json_file_io.time, "sleep", lambda _d: None)  # no real backoff wait

    target = tmp_path / "artifact.data"
    _json_file_io.atomic_write(target, b"payload")
    assert calls["n"] == 3
    assert target.read_bytes() == b"payload"


def test_atomic_write_reraises_after_exhausting_retries(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A persistently-locked destination surfaces the PermissionError (#749)."""

    def always_locked(src: str, dst: str) -> None:
        raise PermissionError(13, "simulated WinError 5: file in use")

    monkeypatch.setattr(_json_file_io.os, "replace", always_locked)
    monkeypatch.setattr(_json_file_io.time, "sleep", lambda _d: None)

    target = tmp_path / "artifact.data"
    with pytest.raises(PermissionError):
        _json_file_io.atomic_write(target, b"payload")
    # The temp file must not be left behind on failure.
    assert not list(tmp_path.glob("._cw_tmp_*"))


def test_atomic_overwrite_never_torn(tmp_path: Path) -> None:
    """A handle overwritten under concurrent readers always reads a whole value.

    ``os.replace`` makes each data-file write atomic, so a reader observes the
    old or new payload in full — never a truncated or interleaved mix.
    """
    store = JsonFileArtifactStore(tmp_path)
    payloads = [b"A" * 2048, b"B" * 2048]
    store.put("h", payloads[0])
    errors: list[bytes] = []

    def _writer() -> None:
        for i in range(300):
            store.put("h", payloads[i % 2])

    def _reader() -> None:
        for _ in range(800):
            value = store.get("h")
            if value not in payloads:
                errors.append(value)

    with ThreadPoolExecutor(max_workers=5) as pool:
        futures = [pool.submit(_writer)] + [pool.submit(_reader) for _ in range(4)]
        for future in futures:
            future.result()

    assert errors == []
    assert store.get("h") in payloads


def test_concurrent_reads_distinct_handles(tmp_path: Path) -> None:
    """Concurrent drilldown reads of distinct handles return the right slice."""
    store = JsonFileArtifactStore(tmp_path)
    for i in range(20):
        store.put(f"h{i}", f"line{i}\nsecond".encode())

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(
                lambda i: store.drilldown(f"h{i}", {"type": "head", "chars": 5}),
                range(20),
            )
        )

    assert results == [f"line{i}"[:5] for i in range(20)]


def test_async_interleaved_store_access(tmp_path: Path) -> None:
    """asyncio.gather interleaving over the store (the async pipeline's pattern)."""
    store = JsonFileArtifactStore(tmp_path)
    handles = [f"a{i}" for i in range(30)]

    async def _run() -> list[bytes]:
        await asyncio.gather(*(asyncio.to_thread(store.put, h, h.encode()) for h in handles))
        return list(await asyncio.gather(*(asyncio.to_thread(store.get, h) for h in handles)))

    assert asyncio.run(_run()) == [h.encode() for h in handles]


def test_proxy_runtime_concurrent_view(tmp_path: Path) -> None:
    """The gateway's read-only tool_view is safe to call concurrently (#458).

    Uses ``:``-bearing handles (the firewall's shape) to also exercise the
    filename-encoding path of ``JsonFileArtifactStore`` (#466).
    """
    manager = ContextManager(artifact_store=JsonFileArtifactStore(tmp_path))
    runtime = ProxyRuntime(StubUpstream([]), context_manager=manager)
    handles = [f"artifact:result:{i}" for i in range(20)]
    for i, handle in enumerate(handles):
        manager.artifact_store.put(handle, f"value-{i}\nrest".encode(), media_type="text/plain")

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(
            pool.map(lambda i: runtime.view(handles[i], {"type": "head", "chars": 7}), range(20))
        )

    assert results == [f"value-{i}"[:7] for i in range(20)]

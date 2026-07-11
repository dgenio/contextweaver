"""Tests for contextweaver.adapters.gateway_status (issue #655)."""

from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from contextweaver.adapters.gateway_status import (
    STALE_AFTER_SECONDS,
    GatewayStatus,
    StatusWriter,
    read_status,
    render_status,
)
from contextweaver.exceptions import ConfigError


class FakeClock:
    """Deterministic injectable monotonic clock."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make_status(**overrides: object) -> GatewayStatus:
    base = GatewayStatus(
        pid=4242,
        started_at="2026-07-11T10:00:00+00:00",
        written_at="2026-07-11T10:05:00+00:00",
        version="0.9.0",
        transport="stdio",
        catalog_hash="deadbeef",
        tool_count=7,
        namespaces=["crm", "billing"],
        upstreams=[{"name": "fs", "healthy": True, "tool_count": 7}],
        counters={"tool_execute": 3, "tool_browse": 5},
        state_dir="/tmp/state",
    )
    return dataclasses.replace(base, **overrides)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Serde
# ---------------------------------------------------------------------------


def test_to_dict_from_dict_round_trip() -> None:
    status = make_status()
    restored = GatewayStatus.from_dict(status.to_dict())
    assert restored.to_dict() == status.to_dict()
    assert restored.pid == 4242
    assert restored.state_dir == "/tmp/state"


def test_to_dict_sorts_namespaces_and_counters() -> None:
    payload = make_status().to_dict()
    assert payload["namespaces"] == ["billing", "crm"]
    assert list(payload["counters"]) == ["tool_browse", "tool_execute"]
    # to_dict must be JSON-compatible.
    json.dumps(payload)


def test_from_dict_defaults_and_none_state_dir() -> None:
    status = GatewayStatus.from_dict({})
    assert status.pid == 0
    assert status.state_dir is None
    assert status.counters == {}


def test_from_dict_rejects_non_mapping_and_bad_fields() -> None:
    with pytest.raises(ConfigError):
        GatewayStatus.from_dict("nope")  # type: ignore[arg-type]
    with pytest.raises(ConfigError, match="invalid gateway status field"):
        GatewayStatus.from_dict({"tool_count": "many"})


# ---------------------------------------------------------------------------
# StatusWriter: write/read round-trip, rate limiting, force, increment
# ---------------------------------------------------------------------------


def test_write_read_round_trip(tmp_path: Path) -> None:
    writer = StatusWriter(tmp_path / "status.json", clock=FakeClock())
    assert writer.update(make_status()) is True
    got = read_status(tmp_path / "status.json")
    assert got.pid == 4242
    assert got.tool_count == 7
    assert got.written_at  # stamped at write time
    assert got.namespaces == ["billing", "crm"]


def test_rapid_updates_are_coalesced_and_force_flushes(tmp_path: Path) -> None:
    clock = FakeClock()
    path = tmp_path / "status.json"
    writer = StatusWriter(path, min_interval_seconds=1.0, clock=clock)

    assert writer.update(make_status(tool_count=1)) is True
    first_bytes = path.read_bytes()
    clock.advance(0.2)
    # Inside the interval: coalesced in memory, file unchanged.
    assert writer.update(make_status(tool_count=2)) is False
    assert path.read_bytes() == first_bytes
    assert read_status(path).tool_count == 1

    # force() flushes the coalesced snapshot immediately.
    writer.force()
    assert read_status(path).tool_count == 2

    # After the interval elapses, the next update writes again.
    clock.advance(2.0)
    assert writer.update(make_status(tool_count=3)) is True
    assert read_status(path).tool_count == 3


def test_coalesced_update_written_on_next_eligible_update(tmp_path: Path) -> None:
    clock = FakeClock()
    path = tmp_path / "status.json"
    writer = StatusWriter(path, min_interval_seconds=1.0, clock=clock)
    writer.update(make_status(tool_count=1))
    clock.advance(0.5)
    writer.update(make_status(tool_count=2))  # coalesced
    assert read_status(path).tool_count == 1
    clock.advance(0.6)  # now eligible
    assert writer.update(make_status(tool_count=4)) is True
    assert read_status(path).tool_count == 4


def test_increment_accumulates_counters(tmp_path: Path) -> None:
    clock = FakeClock()
    writer = StatusWriter(tmp_path / "status.json", min_interval_seconds=0.0, clock=clock)
    writer.update(make_status(counters={}))
    writer.increment(tool_execute=1)
    writer.increment(tool_execute=2, tool_browse=1)
    got = read_status(tmp_path / "status.json")
    assert got.counters == {"tool_browse": 1, "tool_execute": 3}


def test_increment_before_update_raises(tmp_path: Path) -> None:
    writer = StatusWriter(tmp_path / "status.json", clock=FakeClock())
    with pytest.raises(ConfigError, match="call update"):
        writer.increment(tool_execute=1)


def test_force_before_any_update_is_noop(tmp_path: Path) -> None:
    writer = StatusWriter(tmp_path / "status.json", clock=FakeClock())
    writer.force()
    assert not (tmp_path / "status.json").exists()


# ---------------------------------------------------------------------------
# read_status failure modes
# ---------------------------------------------------------------------------


def test_read_status_missing_file_raises_config_error_with_hint(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="--state-dir") as excinfo:
        read_status(tmp_path / "absent.json")
    assert excinfo.value.hint == "is the gateway running with --state-dir?"


def test_read_status_corrupt_file_raises_config_error(tmp_path: Path) -> None:
    path = tmp_path / "status.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError, match="corrupt"):
        read_status(path)
    path.write_text('["a list, not a mapping"]', encoding="utf-8")
    with pytest.raises(ConfigError, match="mapping"):
        read_status(path)


# ---------------------------------------------------------------------------
# render_status
# ---------------------------------------------------------------------------


def test_render_status_fresh_snapshot_has_no_staleness_warning() -> None:
    now = datetime(2026, 7, 11, 10, 5, 10, tzinfo=timezone.utc)
    text = render_status(make_status(), now=now)
    assert "WARNING" not in text
    assert "pid 4242" in text
    assert "uptime 0h 05m 10s" in text
    assert "fs: healthy tools=7" in text
    assert "tool_execute: 3" in text
    assert "billing, crm" in text


def test_render_status_warns_when_written_at_is_stale() -> None:
    now = datetime(2026, 7, 11, 10, 5, 0, tzinfo=timezone.utc) + timedelta(
        seconds=STALE_AFTER_SECONDS + 15
    )
    text = render_status(make_status(), now=now)
    assert "WARNING: status written 45s ago" in text


def test_render_status_warns_on_unparseable_written_at() -> None:
    text = render_status(make_status(written_at="not-a-timestamp"))
    assert "WARNING" in text
    assert "unknown time" in text


def test_render_status_handles_empty_collections() -> None:
    status = GatewayStatus(pid=1)
    text = render_status(status, now=datetime.now(timezone.utc))
    assert "state_dir:  in-memory" in text
    assert text.count("(none)") == 2

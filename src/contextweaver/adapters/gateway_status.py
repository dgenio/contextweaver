"""Gateway status surface for ``mcp status`` (issue #655).

A long-running gateway periodically writes a small JSON :class:`GatewayStatus`
snapshot (typically ``{state_dir}/status.json``) via :class:`StatusWriter`;
``contextweaver mcp status`` (wired by the coordinator) reads it back with
:func:`read_status` and renders it with :func:`render_status` without attaching
to the running process.  Writes are atomic (temp file + ``os.replace``) and
rate-limited through an injectable monotonic clock: updates landing inside the
minimum interval are coalesced in memory and written on the next eligible
update, or immediately via :meth:`StatusWriter.force`.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any

from contextweaver.exceptions import ConfigError

if TYPE_CHECKING:
    from collections.abc import Callable

#: Age of ``written_at`` (seconds) beyond which :func:`render_status` warns
#: that the snapshot looks stale (gateway stopped, wedged, or not writing).
STALE_AFTER_SECONDS: float = 30.0

_MISSING_HINT = "is the gateway running with --state-dir?"


@dataclass
class GatewayStatus:
    """One point-in-time snapshot of a running gateway (issue #655).

    Attributes:
        pid: Operating-system process id of the gateway.
        started_at: ISO-8601 timestamp of process start (UTC recommended).
        written_at: ISO-8601 timestamp of the last status write; stamped by
            :class:`StatusWriter` on every write.
        version: Installed contextweaver version serving the gateway.
        transport: Serving transport (``"stdio"`` / ``"sse"``).
        catalog_hash: Deterministic hash from
            :func:`~contextweaver.routing.manifest.compute_catalog_hash`.
        tool_count: Number of tools in the effective catalog.
        namespaces: Sorted distinct catalog namespaces.
        upstreams: One ``{"name", "healthy", "tool_count"}`` mapping per
            configured upstream, in declared order.
        counters: Per-process operation counters (e.g. ``{"tool_execute": 12}``).
        state_dir: The gateway's ``--state-dir``; ``None`` when in-memory.
    """

    pid: int = 0
    started_at: str = ""
    written_at: str = ""
    version: str = ""
    transport: str = "stdio"
    catalog_hash: str = ""
    tool_count: int = 0
    namespaces: list[str] = field(default_factory=list)
    upstreams: list[dict[str, Any]] = field(default_factory=list)
    counters: dict[str, int] = field(default_factory=dict)
    state_dir: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict (namespaces/counters sorted)."""
        return {
            "pid": self.pid,
            "started_at": self.started_at,
            "written_at": self.written_at,
            "version": self.version,
            "transport": self.transport,
            "catalog_hash": self.catalog_hash,
            "tool_count": self.tool_count,
            "namespaces": sorted(self.namespaces),
            "upstreams": [dict(u) for u in self.upstreams],
            "counters": {k: int(self.counters[k]) for k in sorted(self.counters)},
            "state_dir": self.state_dir,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GatewayStatus:
        """Deserialise from a :meth:`to_dict` payload.

        Raises:
            ConfigError: If *data* is not a mapping or a field is malformed.
        """
        if not isinstance(data, dict):
            raise ConfigError("gateway status payload must be a mapping", hint=_MISSING_HINT)
        try:
            state_dir = data.get("state_dir")
            return cls(
                pid=int(data.get("pid", 0)),
                started_at=str(data.get("started_at", "")),
                written_at=str(data.get("written_at", "")),
                version=str(data.get("version", "")),
                transport=str(data.get("transport", "stdio")),
                catalog_hash=str(data.get("catalog_hash", "")),
                tool_count=int(data.get("tool_count", 0)),
                namespaces=sorted(str(n) for n in data.get("namespaces", [])),
                upstreams=[dict(u) for u in data.get("upstreams", [])],
                counters={str(k): int(v) for k, v in dict(data.get("counters", {})).items()},
                state_dir=str(state_dir) if state_dir is not None else None,
            )
        except (TypeError, ValueError) as exc:
            raise ConfigError(f"invalid gateway status field: {exc}", hint=_MISSING_HINT) from exc


def _atomic_write(path: Path, data: bytes) -> None:
    """Write *data* to *path* atomically (temp file in the same dir + replace).

    Local mirror of :func:`contextweaver.store._json_file_io.atomic_write` —
    that helper is private to the store package and no repository precedent
    exists for importing it across packages, so the tiny primitive is
    duplicated here rather than widening a private API.
    """
    fd, tmp = tempfile.mkstemp(prefix="._cw_status_", dir=str(path.parent))
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


class StatusWriter:
    """Rate-limited atomic writer for a :class:`GatewayStatus` file.

    Args:
        path: Destination status file (parent directory must exist).
        min_interval_seconds: Minimum seconds between two disk writes; updates
            inside the window are coalesced in memory (see :meth:`update`).
        clock: Injectable monotonic clock (seconds), for deterministic tests.
    """

    def __init__(
        self,
        path: str | Path,
        *,
        min_interval_seconds: float = 1.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._path = Path(path)
        self._min_interval = float(min_interval_seconds)
        self._clock = clock
        self._status: GatewayStatus | None = None
        self._last_write_at: float | None = None

    @property
    def path(self) -> Path:
        """The destination status file path."""
        return self._path

    def update(self, status: GatewayStatus) -> bool:
        """Record *status* as the latest snapshot and write it if eligible.

        A first update always writes.  An update inside the rate-limit window
        only replaces the in-memory snapshot; the coalesced snapshot reaches
        disk on the next eligible update or on :meth:`force`.

        Returns:
            ``True`` when written to disk, ``False`` when coalesced in memory.
        """
        self._status = status
        now = self._clock()
        if self._last_write_at is not None and (now - self._last_write_at) < self._min_interval:
            return False
        self._write(now)
        return True

    def increment(self, **counters: int) -> bool:
        """Add *counters* onto the current snapshot's counters and update.

        Builds a fresh :class:`GatewayStatus` copy with each named counter
        incremented and routes it through :meth:`update` (rate limiting
        still applies).

        Returns:
            ``True`` when the resulting snapshot was written to disk.

        Raises:
            ConfigError: If no snapshot was ever recorded via :meth:`update`.
        """
        if self._status is None:
            raise ConfigError("no gateway status recorded yet; call update() before increment()")
        merged = dict(self._status.counters)
        for name, amount in counters.items():
            merged[name] = merged.get(name, 0) + int(amount)
        return self.update(replace(self._status, counters=merged))

    def force(self) -> None:
        """Write the latest snapshot immediately, bypassing the rate limit.

        A no-op when no snapshot has been recorded yet.
        """
        if self._status is not None:
            self._write(self._clock())

    def _write(self, now: float) -> None:
        """Stamp ``written_at`` and atomically persist the current snapshot."""
        if self._status is None:  # pragma: no cover — guarded by both callers
            return
        stamped = replace(self._status, written_at=datetime.now(timezone.utc).isoformat())
        payload = json.dumps(stamped.to_dict(), indent=2, sort_keys=True).encode("utf-8")
        _atomic_write(self._path, payload)
        self._status = stamped
        self._last_write_at = now


def read_status(path: str | Path) -> GatewayStatus:
    """Read a :class:`GatewayStatus` snapshot from *path*.

    Raises:
        ConfigError: If the file is missing, unreadable, or corrupt — with
            the hint ``"is the gateway running with --state-dir?"``.
    """
    file_path = Path(path)
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ConfigError(
            f"cannot read gateway status file {file_path}: {exc}", hint=_MISSING_HINT
        ) from exc
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ConfigError(
            f"corrupt gateway status file {file_path}: {exc}", hint=_MISSING_HINT
        ) from exc
    return GatewayStatus.from_dict(data)


def _parse_iso(value: str) -> datetime | None:
    """Parse an ISO-8601 timestamp; naive values are assumed UTC."""
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return None
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)


def _format_duration(seconds: float) -> str:
    """Render a non-negative duration as ``1h 02m 03s`` (deterministic)."""
    hours, rest = divmod(max(int(seconds), 0), 3600)
    minutes, secs = divmod(rest, 60)
    return f"{hours}h {minutes:02d}m {secs:02d}s"


def render_status(status: GatewayStatus, *, now: datetime | None = None) -> str:
    """Render *status* as a deterministic one-screen text report.

    Args:
        status: The snapshot to render.
        now: Reference time for uptime/staleness; defaults to current UTC
            time (pass a fixed value for deterministic output).

    Returns:
        A newline-terminated multi-line string, including a staleness warning
        when ``written_at`` is older than :data:`STALE_AFTER_SECONDS`.
    """
    ref = now if now is not None else datetime.now(timezone.utc)
    started = _parse_iso(status.started_at)
    uptime = f" (uptime {_format_duration((ref - started).total_seconds())})" if started else ""
    lines = [
        f"contextweaver gateway status (pid {status.pid})",
        f"  version:    {status.version or '-'}",
        f"  transport:  {status.transport or '-'}",
        f"  started:    {status.started_at or '-'}{uptime}",
        f"  written:    {status.written_at or '-'}",
        f"  state_dir:  {status.state_dir or 'in-memory'}",
        f"  catalog:    hash={status.catalog_hash or '-'} tools={status.tool_count}",
        f"  namespaces: {', '.join(sorted(status.namespaces)) or '-'}",
        "  upstreams:",
    ]
    for up in status.upstreams:
        health = "healthy" if up.get("healthy") else "unhealthy"
        lines.append(f"    {up.get('name', '?')}: {health} tools={up.get('tool_count', 0)}")
    if not status.upstreams:
        lines.append("    (none)")
    lines.append("  counters:")
    lines.extend(f"    {name}: {status.counters[name]}" for name in sorted(status.counters))
    if not status.counters:
        lines.append("    (none)")
    written = _parse_iso(status.written_at)
    age = (ref - written).total_seconds() if written else None
    if age is None or age > STALE_AFTER_SECONDS:
        shown = f"{int(age)}s ago" if age is not None else "at an unknown time"
        lines.append(f"  WARNING: status written {shown} — the gateway may be stopped or wedged.")
    return "\n".join(lines) + "\n"


__all__ = ["STALE_AFTER_SECONDS", "GatewayStatus", "StatusWriter", "read_status", "render_status"]

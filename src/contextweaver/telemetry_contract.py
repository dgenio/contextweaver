"""Telemetry handoff contract for downstream consumers (issue #382).

Formalises the :class:`~contextweaver.diagnostics.DiagnosticEvent` v1 JSONL
stream — the file produced by ``contextweaver mcp serve --diagnostics FILE`` —
as a stable, ChainWeaver-consumable contract:

* :data:`EVENT_FAMILIES` names the eight event families and maps each to the
  concrete event-name prefixes that belong to it.
* :func:`classify_event` assigns one event to one family, deterministically.
* :func:`validate_event_dict` checks a raw JSON object against the v1
  envelope shape and flags likely payload leakage.
* :func:`export_jsonl` / :func:`read_jsonl` round-trip an event stream
  without ever raising mid-stream.

The hand-written envelope schema is published at
``schemas/telemetry/v1/diagnostic_event.schema.json``; the human contract
doc is ``docs/telemetry.md``. Events carry metadata only — identifiers,
sizes, timings, argument key names, and error codes — never payload content.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Any

from contextweaver.diagnostics import DiagnosticEvent
from contextweaver.exceptions import ConfigError

#: Contract version. Additive changes stay within v1; a breaking envelope
#: change publishes a new ``schemas/telemetry/v2/`` directory instead.
TELEMETRY_CONTRACT_VERSION: int = 1

#: ``$id`` of the published v1 envelope schema.
TELEMETRY_SCHEMA_ID: str = (
    "https://github.com/dgenio/contextweaver/schemas/telemetry/v1/diagnostic_event.schema.json"
)

#: Attribute values rendering longer than this are flagged as likely payload
#: leakage by :func:`validate_event_dict` (events must carry metadata only).
MAX_ATTRIBUTE_CHARS: int = 2000

_LEAK_MARKER = "likely payload leakage"

#: The eight contract event families -> event-name prefixes (issue #382).
#:
#: Prefixes for ``catalog_inventory``, ``route_request``, ``schema_hydration``,
#: ``execution``, and ``firewall_artifact`` are **live**: they match what
#: :class:`~contextweaver.adapters.gateway_diagnostics.GatewayTelemetry`
#: emits today (``catalog.loaded``, ``browse.completed``/``browse.failed``,
#: ``hydrate.*``, ``execute.*``, ``view.*``). The families listed in
#: :data:`RESERVED_FAMILIES` have no dedicated emitter yet:
#:
#: * ``shortlist`` — shortlist data currently rides on ``browse.completed``
#:   attributes (``card_count``, ``tool_ids``); ``shortlist.`` is reserved
#:   for dedicated events.
#: * ``policy_denial`` — denials currently surface as ``execute.failed``
#:   events with ``attributes.error_code`` of ``"POLICY_DENIED"`` or
#:   ``"AUTH_REQUIRED"`` (issue #373); ``policy.`` is reserved.
#: * ``visibility`` — ``visibility.denied`` is the planned event name for
#:   the visibility gate (``adapters/gateway_visibility.py``, in progress).
EVENT_FAMILIES: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "catalog_inventory": ("catalog.",),
        "route_request": ("browse.",),
        "shortlist": ("shortlist.",),
        "schema_hydration": ("hydrate.",),
        "execution": ("execute.",),
        "firewall_artifact": ("view.",),
        "policy_denial": ("policy.",),
        "visibility": ("visibility.",),
    }
)

#: Families whose prefixes are reserved: nothing emits them yet (see the
#: :data:`EVENT_FAMILIES` notes for where their data lives today).
RESERVED_FAMILIES: frozenset[str] = frozenset({"shortlist", "policy_denial", "visibility"})

#: Prefix -> family lookup, longest prefix first for deterministic matching.
_PREFIX_TO_FAMILY: tuple[tuple[str, str], ...] = tuple(
    sorted(
        ((prefix, family) for family, prefixes in EVENT_FAMILIES.items() for prefix in prefixes),
        key=lambda pair: (-len(pair[0]), pair[0]),
    )
)


def classify_event(event: DiagnosticEvent) -> str | None:
    """Return the contract family for *event*, or ``None`` if unclassified.

    Matching is deterministic: the longest matching prefix in
    :data:`EVENT_FAMILIES` wins (ties broken lexicographically, though the
    v1 prefixes are mutually exclusive).

    Args:
        event: The diagnostic event to classify.

    Returns:
        A family name from :data:`EVENT_FAMILIES`, or ``None`` when no
        prefix matches ``event.event``.
    """
    for prefix, family in _PREFIX_TO_FAMILY:
        if event.event.startswith(prefix):
            return family
    return None


def _check_type(
    data: Mapping[str, Any],
    key: str,
    kinds: tuple[type, ...],
    label: str,
    *,
    required: bool,
    nullable: bool = False,
) -> str | None:
    if key not in data:
        return f"missing required key: {key}" if required else None
    value = data[key]
    if value is None:
        return None if nullable else f"{key} must be {label}, got null"
    if isinstance(value, bool) and bool not in kinds:
        return f"{key} must be {label}, got bool"
    if not isinstance(value, kinds):
        return f"{key} must be {label}, got {type(value).__name__}"
    return None


def validate_event_dict(data: dict[str, Any]) -> list[str]:
    """Validate one raw JSON object against the v1 event envelope.

    Checks the required keys and types of the
    :meth:`~contextweaver.diagnostics.DiagnosticEvent.to_dict` shape, plus a
    payload-content heuristic: any ``attributes`` value whose rendered form
    exceeds :data:`MAX_ATTRIBUTE_CHARS` characters is flagged as likely
    payload leakage (the contract is metadata-only).

    Args:
        data: Decoded JSON object for one event.

    Returns:
        A list of human-readable problems; empty when the event conforms.
    """
    problems: list[str] = []
    checks = (
        _check_type(data, "version", (int,), "an integer", required=True),
        _check_type(data, "event", (str,), "a string", required=True),
        _check_type(data, "timestamp", (str,), "a string", required=True),
        _check_type(data, "success", (bool,), "a boolean", required=True),
        _check_type(data, "session_id", (str,), "a string", required=True),
        _check_type(data, "duration_ms", (int, float), "a number", required=False, nullable=True),
        _check_type(data, "tool_id", (str,), "a string", required=False, nullable=True),
        _check_type(data, "namespace", (str,), "a string", required=False, nullable=True),
        _check_type(data, "attributes", (dict,), "an object", required=False),
    )
    problems.extend(problem for problem in checks if problem is not None)
    if isinstance(data.get("event"), str) and not data["event"]:
        problems.append("event must be a non-empty string")
    # Reject contract-version drift: the published v1 envelope pins
    # ``version: {const: 1}`` (issue #382), so an event declaring any other
    # version does not conform to this contract and must not be silently
    # accepted. ``bool`` is excluded because it subclasses ``int``.
    version = data.get("version")
    if (
        isinstance(version, int)
        and not isinstance(version, bool)
        and version != TELEMETRY_CONTRACT_VERSION
    ):
        problems.append(
            f"unsupported contract version {version} (expected {TELEMETRY_CONTRACT_VERSION})"
        )
    attributes = data.get("attributes")
    if isinstance(attributes, dict):
        for key in sorted(attributes, key=str):
            value = attributes[key]
            try:
                rendered = value if isinstance(value, str) else json.dumps(value, default=str)
            except (TypeError, ValueError):  # pragma: no cover - defensive
                rendered = str(value)
            if len(rendered) > MAX_ATTRIBUTE_CHARS:
                problems.append(
                    f"attributes[{key!r}] renders to {len(rendered)} chars "
                    f"(> {MAX_ATTRIBUTE_CHARS}): {_LEAK_MARKER}"
                )
    return problems


def export_jsonl(events: Iterable[DiagnosticEvent], path: str | Path) -> int:
    """Write *events* to *path* as canonical UTF-8 JSONL.

    Lines are canonical JSON (sorted keys, compact separators), matching
    :class:`~contextweaver.diagnostics.JsonlDiagnosticSink` output, so the
    file round-trips byte-identically through :func:`read_jsonl`.

    Args:
        events: Events to export, written in iteration order.
        path: Destination file path; overwritten if it exists.

    Returns:
        The number of events written.

    Raises:
        ConfigError: If *path* cannot be written.
    """
    target = Path(path)
    written = 0
    try:
        with target.open("w", encoding="utf-8", newline="\n") as stream:
            for event in events:
                line = json.dumps(event.to_dict(), sort_keys=True, separators=(",", ":"))
                stream.write(line + "\n")
                written += 1
    except OSError as exc:
        raise ConfigError(f"cannot write telemetry file {target}: {exc}") from exc
    return written


def read_jsonl(path: str | Path) -> tuple[list[DiagnosticEvent], list[str]]:
    """Read a diagnostic JSONL stream, collecting problems instead of raising.

    Malformed lines (invalid JSON, non-object lines, envelope violations)
    are skipped and reported; the stream is never abandoned mid-read.
    Events that only trip the payload-leak heuristic are still returned,
    with the heuristic finding reported alongside.

    Args:
        path: Source JSONL file path.

    Returns:
        A ``(events, problems)`` pair. Each problem is prefixed with
        ``"<path>:<lineno>:"``.

    Raises:
        ConfigError: If the file itself cannot be read (the only
            non-collectable failure).
    """
    source = Path(path)
    try:
        lines = source.read_text(encoding="utf-8").splitlines()
    except OSError as exc:
        raise ConfigError(f"cannot read telemetry file {source}: {exc}") from exc
    events: list[DiagnosticEvent] = []
    problems: list[str] = []
    for lineno, line in enumerate(lines, 1):
        if not line.strip():
            continue
        try:
            raw = json.loads(line)
        except json.JSONDecodeError as exc:
            problems.append(f"{source}:{lineno}: invalid JSON: {exc}")
            continue
        if not isinstance(raw, dict):
            problems.append(f"{source}:{lineno}: event must be a JSON object")
            continue
        found = validate_event_dict(raw)
        problems.extend(f"{source}:{lineno}: {problem}" for problem in found)
        if any(_LEAK_MARKER not in problem for problem in found):
            continue
        try:
            events.append(DiagnosticEvent.from_dict(raw))
        except (KeyError, TypeError, ValueError) as exc:  # pragma: no cover - defensive
            problems.append(f"{source}:{lineno}: invalid diagnostic event: {exc}")
    return events, problems


__all__ = [
    "EVENT_FAMILIES",
    "MAX_ATTRIBUTE_CHARS",
    "RESERVED_FAMILIES",
    "TELEMETRY_CONTRACT_VERSION",
    "TELEMETRY_SCHEMA_ID",
    "classify_event",
    "export_jsonl",
    "read_jsonl",
    "validate_event_dict",
]

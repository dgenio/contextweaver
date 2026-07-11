"""Record/replay harness for gateway sessions (issue #654).

Pins the gateway's protocol-visible behaviour with committed golden
transcripts: a scripted session is recorded once, checked in, and every test
run replays the same requests against a live gateway and structurally
compares the responses.  A drift in meta-tool definitions, ChoiceCard
rendering, error shapes, or envelope structure fails the pin.

**Recording boundary (stated honestly):** entries are captured at the
gateway *dispatch* boundary — ``tools/list`` payloads and
``(meta-tool name, arguments) → CallToolResult`` pairs — not at the raw
JSON-RPC framing layer.  Framing (ids, envelopes, headers) is owned and
tested by the MCP SDK; everything contextweaver controls about the wire is
the content recorded here.

Volatile fields (timestamps, durations, absolute artifact byte counts that
vary with the token estimator) are normalised via ``volatile_paths`` before
comparison and before writing goldens.

Regenerating a golden after an intentional behaviour change::

    .venv/bin/python -m pytest tests/test_wire_capture.py --regen-goldens

(see ``tests/test_wire_capture.py`` for the fixture wiring).
"""

from __future__ import annotations

import contextlib
import json
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from contextweaver.exceptions import ConfigError

#: Version stamped on transcripts; bump on entry-shape changes.
TRANSCRIPT_VERSION = 1

#: Dispatch signature the recorder/replayer drive: ``(name, args) → result``.
DispatchFn = Callable[[str, dict[str, Any]], Awaitable[dict[str, Any]]]


@dataclass
class TranscriptEntry:
    """One recorded exchange at the gateway dispatch boundary."""

    kind: str  # "list_tools" | "call_tool"
    request: dict[str, Any] = field(default_factory=dict)
    response: dict[str, Any] | list[dict[str, Any]] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {"kind": self.kind, "request": self.request, "response": self.response}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TranscriptEntry:
        """Deserialise from a JSON-compatible dict."""
        kind = data.get("kind")
        if kind not in ("list_tools", "call_tool"):
            raise ConfigError(f"unknown transcript entry kind {kind!r}")
        return cls(kind=kind, request=dict(data.get("request", {})), response=data["response"])


def _delete_path(node: Any, parts: list[str]) -> None:  # noqa: ANN401 - JSON tree
    """Remove ``parts`` (a dotted path, ``*`` = every list item) from *node*."""
    if not parts:
        return
    head, rest = parts[0], parts[1:]
    if head == "*" and isinstance(node, list):
        for item in node:
            _delete_path(item, rest)
    elif isinstance(node, dict) and head in node:
        if rest:
            _delete_path(node[head], rest)
        else:
            del node[head]


def _decode_content_text(response: Any) -> None:  # noqa: ANN401 - JSON tree
    """Decode JSON ``content[*].text`` bodies in place for legible diffs.

    CallToolResult text parts carry JSON payloads (cards, envelopes, error
    shapes); decoding them lets ``volatile_paths`` address fields *inside*
    the body (e.g. ``content.*.text.timestamp``) and makes mismatch reports
    point at fields instead of whole strings.  Non-JSON text is kept as-is.
    """
    if not isinstance(response, dict):
        return
    for part in response.get("content", []):
        if isinstance(part, dict) and isinstance(part.get("text"), str):
            with contextlib.suppress(json.JSONDecodeError):
                part["text"] = json.loads(part["text"])


def normalize_entry(entry: TranscriptEntry, volatile_paths: list[str]) -> TranscriptEntry:
    """Return a copy of *entry* with content bodies decoded and volatile fields removed.

    Paths are dotted and apply to the *response*; ``*`` descends into every
    list element (e.g. ``"content.*.text.timestamp"``).
    """
    payload = json.loads(json.dumps(entry.to_dict()))
    _decode_content_text(payload["response"])
    for path in volatile_paths:
        _delete_path(payload["response"], path.split("."))
    return TranscriptEntry.from_dict(payload)


class WireRecorder:
    """Records a scripted session against a gateway dispatch function."""

    def __init__(
        self,
        dispatch: DispatchFn,
        list_tools: Callable[[], list[dict[str, Any]]],
        *,
        volatile_paths: list[str] | None = None,
    ) -> None:
        self._dispatch = dispatch
        self._list_tools = list_tools
        self._volatile = list(volatile_paths or [])
        self.entries: list[TranscriptEntry] = []

    def record_list_tools(self) -> list[dict[str, Any]]:
        """Record one ``tools/list`` exchange and return the tools."""
        tools = self._list_tools()
        entry = TranscriptEntry(kind="list_tools", request={}, response=tools)
        self.entries.append(normalize_entry(entry, self._volatile))
        return tools

    async def record_call(self, name: str, args: dict[str, Any]) -> dict[str, Any]:
        """Record one meta-tool call and return its raw result."""
        result = await self._dispatch(name, args)
        entry = TranscriptEntry(
            kind="call_tool", request={"name": name, "arguments": args}, response=result
        )
        self.entries.append(normalize_entry(entry, self._volatile))
        return result


def save_transcript(entries: list[TranscriptEntry], path: str | Path) -> None:
    """Write a stable-diff transcript file (sorted keys, indent 2)."""
    payload = {
        "version": TRANSCRIPT_VERSION,
        "entries": [entry.to_dict() for entry in entries],
    }
    Path(path).write_text(json.dumps(payload, sort_keys=True, indent=2) + "\n", encoding="utf-8")


def load_transcript(path: str | Path) -> list[TranscriptEntry]:
    """Load a transcript written by :func:`save_transcript`.

    Raises:
        ConfigError: On a missing file, malformed JSON, or version mismatch.
    """
    target = Path(path)
    if not target.exists():
        raise ConfigError(f"transcript {target} does not exist")
    try:
        payload = json.loads(target.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ConfigError(f"transcript {target} is not valid JSON: {exc}") from exc
    if payload.get("version") != TRANSCRIPT_VERSION:
        raise ConfigError(f"transcript version {payload.get('version')!r} != {TRANSCRIPT_VERSION}")
    return [TranscriptEntry.from_dict(entry) for entry in payload.get("entries", [])]


def _diff_json(expected: Any, actual: Any, path: str, problems: list[str]) -> None:  # noqa: ANN401
    """Structural comparison collecting human-readable mismatches."""
    if isinstance(expected, dict) and isinstance(actual, dict):
        for key in sorted(set(expected) | set(actual)):
            if key not in expected:
                problems.append(f"{path}.{key}: unexpected key in live response")
            elif key not in actual:
                problems.append(f"{path}.{key}: missing from live response")
            else:
                _diff_json(expected[key], actual[key], f"{path}.{key}", problems)
    elif isinstance(expected, list) and isinstance(actual, list):
        if len(expected) != len(actual):
            problems.append(f"{path}: length {len(actual)} != recorded {len(expected)}")
        for index, (exp, act) in enumerate(zip(expected, actual, strict=False)):
            _diff_json(exp, act, f"{path}[{index}]", problems)
    elif expected != actual:
        problems.append(f"{path}: {actual!r} != recorded {expected!r}")


async def replay_and_verify(
    transcript: list[TranscriptEntry],
    dispatch: DispatchFn,
    list_tools: Callable[[], list[dict[str, Any]]],
    *,
    volatile_paths: list[str] | None = None,
) -> list[str]:
    """Re-drive every recorded request and compare responses structurally.

    Returns:
        Mismatch descriptions; an empty list means the pin holds.
    """
    volatile = list(volatile_paths or [])
    problems: list[str] = []
    for index, recorded in enumerate(transcript):
        if recorded.kind == "list_tools":
            live: Any = list_tools()
            entry = TranscriptEntry(kind="list_tools", request={}, response=live)
        else:
            name = str(recorded.request.get("name", ""))
            args = dict(recorded.request.get("arguments", {}))
            live = await dispatch(name, args)
            entry = TranscriptEntry(kind="call_tool", request=recorded.request, response=live)
        normalized = normalize_entry(entry, volatile)
        _diff_json(
            recorded.response, normalized.response, f"entry[{index}]({recorded.kind})", problems
        )
    return problems


__all__ = [
    "TRANSCRIPT_VERSION",
    "TranscriptEntry",
    "WireRecorder",
    "load_transcript",
    "normalize_entry",
    "replay_and_verify",
    "save_transcript",
]

"""Tests for the gateway record/replay harness + committed golden (issue #654).

Regenerate the golden after an intentional gateway behaviour change:

    REGEN_WIRE_GOLDENS=1 .venv/bin/python -m pytest tests/test_wire_capture.py -q
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from contextweaver.adapters.mcp_gateway import dispatch_meta_tool, make_gateway_meta_tools
from contextweaver.adapters.mcp_upstream import StubUpstream
from contextweaver.adapters.proxy_runtime import ProxyRuntime
from contextweaver.adapters.wire_capture import (
    TranscriptEntry,
    WireRecorder,
    load_transcript,
    replay_and_verify,
    save_transcript,
)
from contextweaver.exceptions import ConfigError

GOLDEN = Path(__file__).parent / "fixtures" / "wire" / "gateway_session_v1.json"

#: Fields that vary run-to-run (routing-decision timestamps).
VOLATILE = ["content.*.text.timestamp", "content.*.text.decision.timestamp"]


def _tool_defs() -> list[dict[str, Any]]:
    return [
        {
            "name": "github.create_issue",
            "description": "Open a new GitHub issue with a title.",
            "inputSchema": {
                "type": "object",
                "properties": {"title": {"type": "string"}},
                "required": ["title"],
            },
        },
        {
            "name": "github.list_repos",
            "description": "List repositories for the authenticated user.",
            "inputSchema": {"type": "object", "properties": {}},
        },
    ]


async def _handler(name: str, args: dict[str, Any]) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": f"called {name}"}], "isError": False}


def _runtime() -> ProxyRuntime:
    runtime = ProxyRuntime(StubUpstream(_tool_defs(), handler=_handler))
    runtime.register_tool_defs_sync(_tool_defs())
    return runtime


async def _record_session() -> WireRecorder:
    """The scripted golden session: list → browse → execute → view error."""
    runtime = _runtime()
    recorder = WireRecorder(
        lambda name, args: dispatch_meta_tool(runtime, name, args),
        lambda: make_gateway_meta_tools(runtime),
        volatile_paths=VOLATILE,
    )
    recorder.record_list_tools()
    browse = await recorder.record_call("tool_browse", {"query": "open a github issue"})
    body = json.loads(browse["content"][0]["text"])
    cards = body if isinstance(body, list) else body.get("choice_cards", [])
    tool_id = cards[0]["id"] if cards and isinstance(cards[0], dict) else "github.create_issue"
    await recorder.record_call("tool_execute", {"tool_id": tool_id, "arguments": {"title": "hi"}})
    await recorder.record_call("tool_view", {"artifact_ref": "artifact:missing", "selector": {}})
    return recorder


async def test_golden_pin_holds() -> None:
    recorder = await _record_session()
    if os.environ.get("REGEN_WIRE_GOLDENS"):
        GOLDEN.parent.mkdir(parents=True, exist_ok=True)
        save_transcript(recorder.entries, GOLDEN)
    assert GOLDEN.exists(), "golden missing — run with REGEN_WIRE_GOLDENS=1 once"
    transcript = load_transcript(GOLDEN)
    runtime = _runtime()
    problems = await replay_and_verify(
        transcript,
        lambda name, args: dispatch_meta_tool(runtime, name, args),
        lambda: make_gateway_meta_tools(runtime),
        volatile_paths=VOLATILE,
    )
    assert problems == []


async def test_recording_is_deterministic() -> None:
    first = await _record_session()
    second = await _record_session()
    assert [e.to_dict() for e in first.entries] == [e.to_dict() for e in second.entries]


async def test_mutated_transcript_is_detected() -> None:
    transcript = load_transcript(GOLDEN)
    mutated = json.loads(json.dumps(transcript[0].to_dict()))
    mutated["response"][0]["description"] = "tampered"
    transcript[0] = TranscriptEntry.from_dict(mutated)
    runtime = _runtime()
    problems = await replay_and_verify(
        transcript,
        lambda name, args: dispatch_meta_tool(runtime, name, args),
        lambda: make_gateway_meta_tools(runtime),
        volatile_paths=VOLATILE,
    )
    assert problems and any("description" in p for p in problems)


async def test_save_load_round_trip_is_byte_stable(tmp_path: Path) -> None:
    recorder = await _record_session()
    first_path, second_path = tmp_path / "a.json", tmp_path / "b.json"
    save_transcript(recorder.entries, first_path)
    save_transcript(load_transcript(first_path), second_path)
    assert first_path.read_bytes() == second_path.read_bytes()


def test_load_rejects_missing_and_malformed(tmp_path: Path) -> None:
    with pytest.raises(ConfigError):
        load_transcript(tmp_path / "absent.json")
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ConfigError):
        load_transcript(bad)
    wrong_version = tmp_path / "v99.json"
    wrong_version.write_text(json.dumps({"version": 99, "entries": []}), encoding="utf-8")
    with pytest.raises(ConfigError):
        load_transcript(wrong_version)

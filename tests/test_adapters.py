"""Tests for contextweaver adapters (MCP and A2A).

Verifies that MCP and A2A adapter functions correctly convert external
protocol data into contextweaver-native types (SelectableItem,
ResultEnvelope, ContextItem).
"""

from __future__ import annotations

import json
import tempfile
from pathlib import Path

from contextweaver.adapters.a2a import (
    agent_response_to_envelope,
    agent_to_item,
    load_a2a_session_jsonl,
)
from contextweaver.adapters.mcp import (
    load_mcp_session_jsonl,
    mcp_result_to_envelope,
    mcp_tool_to_item,
)
from contextweaver.types import ItemKind, ResultEnvelope, SelectableItem

# ---------------------------------------------------------------------------
# MCP adapter tests
# ---------------------------------------------------------------------------


def test_mcp_tool_to_item_basic() -> None:
    """mcp_tool_to_item returns a SelectableItem with correct fields."""
    schema = {"name": "read_file", "description": "Read a file from disk"}
    item = mcp_tool_to_item(schema)

    assert isinstance(item, SelectableItem)
    assert item.id == "mcp.read_file"
    assert item.kind == "tool"
    assert item.name == "read_file"
    assert item.description == "Read a file from disk"
    assert item.namespace == "mcp"
    assert item.metadata["source"] == "mcp"


def test_mcp_tool_to_item_with_annotations() -> None:
    """mcp_tool_to_item propagates annotations (tags, sideEffects, costHint)."""
    schema = {
        "name": "write_file",
        "description": "Write content to a file",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}},
        "annotations": {
            "tags": ["fs", "write"],
            "sideEffects": True,
            "costHint": "medium",
        },
    }
    item = mcp_tool_to_item(schema)

    assert item.tags == ["fs", "write"]
    assert item.side_effects is True
    assert item.cost_hint == "medium"
    assert item.args_schema == {
        "type": "object",
        "properties": {"path": {"type": "string"}},
    }


def test_mcp_tool_to_item_defaults() -> None:
    """mcp_tool_to_item uses sensible defaults for missing fields."""
    item = mcp_tool_to_item({})

    assert item.name == "unknown"
    assert item.description == ""
    assert item.tags == []
    assert item.side_effects is False
    assert item.cost_hint == "low"


def test_mcp_result_to_envelope_ok() -> None:
    """mcp_result_to_envelope converts a successful result to a ResultEnvelope."""
    result = {
        "content": [
            {"type": "text", "text": "Hello world"},
        ],
    }
    envelope = mcp_result_to_envelope(result)

    assert isinstance(envelope, ResultEnvelope)
    assert envelope.status == "ok"
    assert isinstance(envelope.summary, str)
    assert len(envelope.summary) > 0
    assert envelope.provenance == {"source": "mcp"}


def test_mcp_result_to_envelope_error() -> None:
    """mcp_result_to_envelope sets status='error' when isError is True."""
    result = {
        "isError": True,
        "content": [{"type": "text", "text": "File not found"}],
    }
    envelope = mcp_result_to_envelope(result)

    assert envelope.status == "error"


def test_mcp_result_to_envelope_extracts_facts() -> None:
    """mcp_result_to_envelope returns structured facts from the extractor."""
    result = {
        "content": [{"type": "text", "text": "The total count is 42."}],
    }
    envelope = mcp_result_to_envelope(result)

    assert isinstance(envelope.facts, dict)


def test_load_mcp_session_jsonl() -> None:
    """load_mcp_session_jsonl parses JSONL lines into ContextItems."""
    lines = [
        {"type": "user_turn", "id": "u1", "text": "Hello", "timestamp": 1.0},
        {
            "type": "tool_call",
            "id": "tc1",
            "tool_name": "search",
            "args": {"q": "test"},
            "timestamp": 2.0,
        },
        {
            "type": "tool_result",
            "id": "tr1",
            "tool_call_id": "tc1",
            "content": "found it",
            "timestamp": 3.0,
        },
        {"type": "agent_msg", "id": "a1", "text": "Here you go", "timestamp": 4.0},
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")
        path = f.name

    items = load_mcp_session_jsonl(path)
    Path(path).unlink()

    assert len(items) == 4
    assert items[0].kind == ItemKind.USER_TURN
    assert items[0].text == "Hello"
    assert items[1].kind == ItemKind.TOOL_CALL
    assert items[2].kind == ItemKind.TOOL_RESULT
    assert items[2].parent_id == "tc1"
    assert items[3].kind == ItemKind.AGENT_MSG


def test_load_mcp_session_jsonl_skips_unknown_types() -> None:
    """load_mcp_session_jsonl skips lines with unrecognised event types."""
    lines = [
        {"type": "unknown_event", "id": "x1", "text": "skip me"},
        {"type": "user_turn", "id": "u1", "text": "keep me", "timestamp": 1.0},
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")
        path = f.name

    items = load_mcp_session_jsonl(path)
    Path(path).unlink()

    assert len(items) == 1
    assert items[0].id == "u1"


# ---------------------------------------------------------------------------
# A2A adapter tests
# ---------------------------------------------------------------------------


def test_agent_to_item_basic() -> None:
    """agent_to_item returns a SelectableItem with correct fields."""
    agent_info = {"name": "code_reviewer", "description": "Reviews code for bugs"}
    item = agent_to_item(agent_info)

    assert isinstance(item, SelectableItem)
    assert item.id == "a2a.code_reviewer"
    assert item.kind == "agent"
    assert item.name == "code_reviewer"
    assert item.description == "Reviews code for bugs"
    assert item.namespace == "a2a"
    assert item.metadata["source"] == "a2a"


def test_agent_to_item_with_skills() -> None:
    """agent_to_item extracts skill names into tags."""
    agent_info = {
        "name": "assistant",
        "description": "General assistant",
        "skills": [
            {"name": "search"},
            {"name": "summarize"},
        ],
    }
    item = agent_to_item(agent_info)

    assert item.tags == ["search", "summarize"]


def test_agent_to_item_defaults() -> None:
    """agent_to_item uses sensible defaults for missing fields."""
    item = agent_to_item({})

    assert item.name == "unknown"
    assert item.description == ""
    assert item.tags == []


def test_agent_response_to_envelope_ok() -> None:
    """agent_response_to_envelope converts a successful response."""
    response = {"status": "ok", "text": "The answer is 42."}
    envelope = agent_response_to_envelope(response)

    assert isinstance(envelope, ResultEnvelope)
    assert envelope.status == "ok"
    assert isinstance(envelope.summary, str)
    assert len(envelope.summary) > 0
    assert envelope.provenance == {"source": "a2a"}
    assert envelope.facts == {"source": "a2a"}


def test_agent_response_to_envelope_error_status() -> None:
    """agent_response_to_envelope maps error status correctly."""
    response = {"status": "error", "text": "Something went wrong"}
    envelope = agent_response_to_envelope(response)

    assert envelope.status == "error"


def test_agent_response_to_envelope_normalises_unknown_status() -> None:
    """agent_response_to_envelope normalises unrecognised statuses to 'ok'."""
    response = {"status": "unknown_status", "text": "Some text"}
    envelope = agent_response_to_envelope(response)

    assert envelope.status == "ok"


def test_load_a2a_session_jsonl() -> None:
    """load_a2a_session_jsonl parses JSONL lines into ContextItems."""
    lines = [
        {"type": "user_turn", "id": "u1", "text": "Hello", "timestamp": 1.0},
        {"type": "agent_msg", "id": "a1", "text": "Hi there", "timestamp": 2.0},
        {"type": "tool_call", "id": "tc1", "text": "calling tool", "timestamp": 3.0},
        {
            "type": "tool_result",
            "id": "tr1",
            "tool_call_id": "tc1",
            "text": "result",
            "timestamp": 4.0,
        },
    ]
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        for line in lines:
            f.write(json.dumps(line) + "\n")
        path = f.name

    items = load_a2a_session_jsonl(path)
    Path(path).unlink()

    assert len(items) == 4
    assert items[0].kind == ItemKind.USER_TURN
    assert items[1].kind == ItemKind.AGENT_MSG
    assert items[2].kind == ItemKind.TOOL_CALL
    assert items[3].kind == ItemKind.TOOL_RESULT
    assert items[3].parent_id == "tc1"


def test_load_a2a_session_jsonl_skips_blank_lines() -> None:
    """load_a2a_session_jsonl ignores blank lines in the JSONL file."""
    content = (
        json.dumps({"type": "user_turn", "id": "u1", "text": "Hi", "timestamp": 1.0})
        + "\n\n\n"
        + json.dumps({"type": "agent_msg", "id": "a1", "text": "Hey", "timestamp": 2.0})
        + "\n"
    )
    with tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False) as f:
        f.write(content)
        path = f.name

    items = load_a2a_session_jsonl(path)
    Path(path).unlink()

    assert len(items) == 2

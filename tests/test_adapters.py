"""Tests for contextweaver adapters (MCP and A2A)."""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from contextweaver.adapters.a2a import (
    a2a_agent_to_selectable,
    a2a_result_to_envelope,
    load_a2a_session_jsonl,
)
from contextweaver.adapters.mcp import (
    load_mcp_session_jsonl,
    mcp_result_to_envelope,
    mcp_tool_to_selectable,
)
from contextweaver.exceptions import CatalogError

# ---------------------------------------------------------------------------
# MCP adapter — mcp_tool_to_selectable
# ---------------------------------------------------------------------------


def test_mcp_tool_to_selectable_basic() -> None:
    tool_def = {"name": "search", "description": "Search the database"}
    item = mcp_tool_to_selectable(tool_def)
    assert item.id == "mcp:search"
    assert item.kind == "tool"
    assert item.name == "search"
    assert item.description == "Search the database"
    assert item.namespace == "mcp"
    assert "mcp" in item.tags


def test_mcp_tool_to_selectable_with_annotations() -> None:
    tool_def = {
        "name": "read_file",
        "description": "Read a file",
        "annotations": {
            "readOnlyHint": True,
            "costHint": 0.1,
        },
    }
    item = mcp_tool_to_selectable(tool_def)
    assert item.side_effects is False
    assert item.cost_hint == 0.1
    assert "read-only" in item.tags


def test_mcp_tool_to_selectable_with_destructive_hint() -> None:
    tool_def = {
        "name": "delete_file",
        "description": "Delete a file",
        "annotations": {"destructiveHint": True},
    }
    item = mcp_tool_to_selectable(tool_def)
    assert "destructive" in item.tags
    assert item.side_effects is True


def test_mcp_tool_to_selectable_with_schema() -> None:
    tool_def = {
        "name": "query",
        "description": "Query data",
        "inputSchema": {"type": "object", "properties": {"sql": {"type": "string"}}},
    }
    item = mcp_tool_to_selectable(tool_def)
    assert item.args_schema == {"type": "object", "properties": {"sql": {"type": "string"}}}


def test_mcp_tool_to_selectable_missing_name() -> None:
    with pytest.raises(CatalogError, match="missing required fields"):
        mcp_tool_to_selectable({"description": "no name"})


def test_mcp_tool_to_selectable_missing_description() -> None:
    with pytest.raises(CatalogError, match="missing required fields"):
        mcp_tool_to_selectable({"name": "tool"})


# ---------------------------------------------------------------------------
# MCP adapter — mcp_result_to_envelope
# ---------------------------------------------------------------------------


def test_mcp_result_to_envelope_text_content() -> None:
    result = {
        "content": [{"type": "text", "text": "status: ok\ncount: 42"}],
    }
    env = mcp_result_to_envelope(result, "search")
    assert env.status == "ok"
    assert "42" in env.summary
    assert env.provenance["protocol"] == "mcp"
    assert env.provenance["tool"] == "search"


def test_mcp_result_to_envelope_error_flag() -> None:
    result = {
        "content": [{"type": "text", "text": "error occurred"}],
        "isError": True,
    }
    env = mcp_result_to_envelope(result, "tool")
    assert env.status == "error"


def test_mcp_result_to_envelope_image_content() -> None:
    result = {
        "content": [
            {"type": "image", "data": "base64data", "mimeType": "image/png"},
        ],
    }
    env = mcp_result_to_envelope(result, "screenshot")
    assert len(env.artifacts) == 1
    assert env.artifacts[0].media_type == "image/png"


def test_mcp_result_to_envelope_resource_content() -> None:
    result = {
        "content": [
            {
                "type": "resource",
                "resource": {
                    "uri": "file:///data.csv",
                    "mimeType": "text/csv",
                    "text": "a,b\n1,2",
                },
            }
        ],
    }
    env = mcp_result_to_envelope(result, "read_file")
    assert len(env.artifacts) == 1
    assert env.artifacts[0].media_type == "text/csv"
    assert "a,b" in env.summary


def test_mcp_result_to_envelope_empty_content() -> None:
    result: dict[str, object] = {"content": []}
    env = mcp_result_to_envelope(result, "noop")
    assert env.summary == "(no content)"
    assert env.status == "ok"


def test_mcp_result_to_envelope_multiple_parts() -> None:
    result = {
        "content": [
            {"type": "text", "text": "part 1"},
            {"type": "text", "text": "part 2"},
        ],
    }
    env = mcp_result_to_envelope(result, "multi")
    assert "part 1" in env.summary
    assert "part 2" in env.summary


# ---------------------------------------------------------------------------
# MCP adapter — load_mcp_session_jsonl
# ---------------------------------------------------------------------------


def test_load_mcp_session_jsonl() -> None:
    lines = [
        json.dumps({"id": "tc1", "type": "tool_call", "text": "call search"}),
        json.dumps(
            {"id": "tr1", "type": "tool_result", "text": "42 rows", "parent_id": "tc1"}
        ),
    ]
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    ) as f:
        f.write("\n".join(lines))
        f.flush()
        path = f.name
    try:
        items = load_mcp_session_jsonl(path)
        assert len(items) == 2
        assert items[0].kind.value == "tool_call"
        assert items[1].parent_id == "tc1"
    finally:
        os.unlink(path)


def test_load_mcp_session_jsonl_missing_file() -> None:
    with pytest.raises(CatalogError, match="Cannot read"):
        load_mcp_session_jsonl("/nonexistent/file.jsonl")


def test_load_mcp_session_jsonl_invalid_json() -> None:
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    ) as f:
        f.write("not json\n")
        f.flush()
        path = f.name
    try:
        with pytest.raises(CatalogError, match="Invalid JSON"):
            load_mcp_session_jsonl(path)
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# A2A adapter — a2a_agent_to_selectable
# ---------------------------------------------------------------------------


def test_a2a_agent_to_selectable_basic() -> None:
    card = {"name": "summarizer", "description": "Summarizes documents"}
    item = a2a_agent_to_selectable(card)
    assert item.id == "a2a:summarizer"
    assert item.kind == "agent"
    assert item.namespace == "a2a"
    assert "a2a" in item.tags


def test_a2a_agent_to_selectable_with_skills() -> None:
    card = {
        "name": "analyst",
        "description": "Data analyst agent",
        "skills": [
            {"id": "s1", "name": "analyze", "description": "Analyze data"},
            {"id": "s2", "name": "visualize", "description": "Create charts"},
        ],
    }
    item = a2a_agent_to_selectable(card)
    assert "analyze" in item.tags
    assert "visualize" in item.tags
    assert item.metadata["skills"] == card["skills"]


def test_a2a_agent_to_selectable_with_modes() -> None:
    card = {
        "name": "agent",
        "description": "An agent",
        "defaultInputModes": ["text/plain"],
        "defaultOutputModes": ["text/plain", "application/json"],
    }
    item = a2a_agent_to_selectable(card)
    assert item.metadata["input_modes"] == ["text/plain"]
    assert item.metadata["output_modes"] == ["text/plain", "application/json"]


def test_a2a_agent_to_selectable_missing_name() -> None:
    with pytest.raises(CatalogError, match="missing required fields"):
        a2a_agent_to_selectable({"description": "no name"})


def test_a2a_agent_to_selectable_missing_description() -> None:
    with pytest.raises(CatalogError, match="missing required fields"):
        a2a_agent_to_selectable({"name": "agent"})


# ---------------------------------------------------------------------------
# A2A adapter — a2a_result_to_envelope
# ---------------------------------------------------------------------------


def test_a2a_result_to_envelope_completed() -> None:
    result = {
        "status": {"state": "completed"},
        "artifacts": [
            {"parts": [{"type": "text", "text": "Analysis complete: 42 rows processed"}]}
        ],
    }
    env = a2a_result_to_envelope(result, "analyst")
    assert env.status == "ok"
    assert "42" in env.summary
    assert env.provenance["protocol"] == "a2a"
    assert env.provenance["state"] == "completed"


def test_a2a_result_to_envelope_failed() -> None:
    result = {
        "status": {"state": "failed", "message": "timeout"},
    }
    env = a2a_result_to_envelope(result, "agent")
    assert env.status == "error"
    assert "timeout" in env.summary


def test_a2a_result_to_envelope_partial() -> None:
    result = {
        "status": {"state": "working"},
        "artifacts": [{"parts": [{"type": "text", "text": "in progress"}]}],
    }
    env = a2a_result_to_envelope(result, "agent")
    assert env.status == "partial"


def test_a2a_result_to_envelope_data_artifact() -> None:
    result = {
        "status": {"state": "completed"},
        "artifacts": [
            {
                "parts": [
                    {"type": "data", "data": "base64", "mimeType": "image/png"},
                ]
            }
        ],
    }
    env = a2a_result_to_envelope(result, "painter")
    assert len(env.artifacts) == 1
    assert env.artifacts[0].media_type == "image/png"


def test_a2a_result_to_envelope_no_artifacts() -> None:
    result = {"status": {"state": "completed"}}
    env = a2a_result_to_envelope(result, "agent")
    assert env.status == "ok"
    assert "(completed)" in env.summary


# ---------------------------------------------------------------------------
# A2A adapter — load_a2a_session_jsonl
# ---------------------------------------------------------------------------


def test_load_a2a_session_jsonl() -> None:
    lines = [
        json.dumps({"id": "u1", "type": "user_turn", "text": "summarize this"}),
        json.dumps(
            {"id": "a1", "type": "agent_msg", "text": "Here is the summary", "parent_id": "u1"}
        ),
    ]
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    ) as f:
        f.write("\n".join(lines))
        f.flush()
        path = f.name
    try:
        items = load_a2a_session_jsonl(path)
        assert len(items) == 2
        assert items[0].kind.value == "user_turn"
        assert items[1].parent_id == "u1"
    finally:
        os.unlink(path)


def test_load_a2a_session_jsonl_missing_file() -> None:
    with pytest.raises(CatalogError, match="Cannot read"):
        load_a2a_session_jsonl("/nonexistent/file.jsonl")

"""Tests for contextweaver adapters (MCP, A2A, and FastMCP)."""

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
from contextweaver.adapters.fastmcp import (
    fastmcp_tool_to_selectable,
    fastmcp_tools_to_catalog,
    infer_fastmcp_namespace,
)
from contextweaver.adapters.mcp import (
    infer_namespace,
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
    assert item.namespace == "mcp"  # no prefix → fallback
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


def test_mcp_tool_to_selectable_namespace_dotted() -> None:
    tool_def = {"name": "github.create_issue", "description": "Create an issue"}
    item = mcp_tool_to_selectable(tool_def)
    assert item.namespace == "github"


def test_mcp_tool_to_selectable_namespace_slash() -> None:
    tool_def = {"name": "filesystem/read", "description": "Read a file"}
    item = mcp_tool_to_selectable(tool_def)
    assert item.namespace == "filesystem"


def test_mcp_tool_to_selectable_namespace_underscore_3_segments() -> None:
    tool_def = {"name": "slack_send_message", "description": "Send a Slack message"}
    item = mcp_tool_to_selectable(tool_def)
    assert item.namespace == "slack"


def test_mcp_tool_to_selectable_namespace_underscore_2_segments_fallback() -> None:
    tool_def = {"name": "read_file", "description": "Read a file"}
    item = mcp_tool_to_selectable(tool_def)
    assert item.namespace == "mcp"  # only 2 segments → fallback


def test_mcp_tool_to_selectable_missing_name() -> None:
    with pytest.raises(CatalogError, match="missing required fields"):
        mcp_tool_to_selectable({"description": "no name"})


def test_mcp_tool_to_selectable_missing_description() -> None:
    with pytest.raises(CatalogError, match="missing required fields"):
        mcp_tool_to_selectable({"name": "tool"})


# ---------------------------------------------------------------------------
# MCP adapter — infer_namespace
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool_name", "expected"),
    [
        ("github.create_issue", "github"),
        ("github.repos.list", "github"),
        ("filesystem/read", "filesystem"),
        ("db/tables/list", "db"),
        ("slack_send_message", "slack"),
        ("aws_s3_list_buckets", "aws"),
        ("read_file", "mcp"),
        ("search", "mcp"),
        ("", "mcp"),
        (".hidden", "mcp"),
        ("/path", "mcp"),
        ("_a_b", "mcp"),
    ],
)
def test_infer_namespace(tool_name: str, expected: str) -> None:
    assert infer_namespace(tool_name) == expected


# ---------------------------------------------------------------------------
# MCP adapter — mcp_result_to_envelope
# ---------------------------------------------------------------------------


def test_mcp_result_to_envelope_text_content() -> None:
    result = {
        "content": [{"type": "text", "text": "status: ok\ncount: 42"}],
    }
    env, binaries, full_text = mcp_result_to_envelope(result, "search")
    assert env.status == "ok"
    assert "42" in env.summary
    assert env.provenance["protocol"] == "mcp"
    assert env.provenance["tool"] == "search"
    assert binaries == {}  # text-only → no binary data
    assert full_text == "status: ok\ncount: 42"


def test_mcp_result_to_envelope_error_flag() -> None:
    result = {
        "content": [{"type": "text", "text": "error occurred"}],
        "isError": True,
    }
    env, _binaries, _full_text = mcp_result_to_envelope(result, "tool")
    assert env.status == "error"


def test_mcp_result_to_envelope_image_content() -> None:
    import base64

    png_bytes = b"\x89PNG_fake_image"
    b64_data = base64.b64encode(png_bytes).decode()
    result = {
        "content": [
            {"type": "image", "data": b64_data, "mimeType": "image/png"},
        ],
    }
    env, binaries, _full_text = mcp_result_to_envelope(result, "screenshot")
    assert len(env.artifacts) == 1
    assert env.artifacts[0].media_type == "image/png"
    # Binary data is base64-decoded
    handle = "mcp:screenshot:image:0"
    assert handle in binaries
    raw, mime, label = binaries[handle]
    assert raw == png_bytes
    assert mime == "image/png"
    assert "screenshot" in label


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
    env, binaries, full_text = mcp_result_to_envelope(result, "read_file")
    assert len(env.artifacts) == 1
    assert full_text == "a,b\n1,2"  # resource text included in full_text
    assert env.artifacts[0].media_type == "text/csv"
    assert "a,b" in env.summary
    # Resource text is stored as UTF-8 bytes
    handle = "mcp:read_file:resource:0"
    assert handle in binaries
    raw, mime, _label = binaries[handle]
    assert raw == b"a,b\n1,2"
    assert mime == "text/csv"


def test_mcp_result_to_envelope_empty_content() -> None:
    result: dict[str, object] = {"content": []}
    env, binaries, full_text = mcp_result_to_envelope(result, "noop")
    assert env.summary == "(no content)"
    assert full_text == "(no content)"
    assert env.status == "ok"
    assert binaries == {}


def test_mcp_result_to_envelope_multiple_parts() -> None:
    result = {
        "content": [
            {"type": "text", "text": "part 1"},
            {"type": "text", "text": "part 2"},
        ],
    }
    env, binaries, full_text = mcp_result_to_envelope(result, "multi")
    assert "part 1" in env.summary
    assert "part 2" in env.summary
    assert full_text == "part 1\npart 2"
    assert binaries == {}  # text-only


# ---------------------------------------------------------------------------
# MCP adapter — outputSchema support
# ---------------------------------------------------------------------------


def test_mcp_tool_to_selectable_with_output_schema() -> None:
    schema = {"type": "object", "properties": {"result": {"type": "string"}}}
    tool_def = {
        "name": "query",
        "description": "Query data",
        "outputSchema": schema,
    }
    item = mcp_tool_to_selectable(tool_def)
    assert item.output_schema == schema


def test_mcp_tool_to_selectable_without_output_schema() -> None:
    tool_def = {"name": "search", "description": "Search the database"}
    item = mcp_tool_to_selectable(tool_def)
    assert item.output_schema is None


# ---------------------------------------------------------------------------
# MCP adapter — audio content type
# ---------------------------------------------------------------------------


def test_mcp_result_to_envelope_audio_content() -> None:
    import base64

    wav_bytes = b"RIFF_fake_audio_data"
    b64_data = base64.b64encode(wav_bytes).decode()
    result = {
        "content": [
            {"type": "audio", "data": b64_data, "mimeType": "audio/wav"},
        ],
    }
    env, binaries, _full_text = mcp_result_to_envelope(result, "transcribe")
    assert len(env.artifacts) == 1
    assert env.artifacts[0].media_type == "audio/wav"
    assert env.artifacts[0].label == "audio from transcribe"
    handle = "mcp:transcribe:audio:0"
    assert handle in binaries
    raw, mime, label = binaries[handle]
    assert raw == wav_bytes
    assert mime == "audio/wav"


def test_mcp_result_to_envelope_audio_invalid_base64() -> None:
    result = {
        "content": [
            {"type": "audio", "data": "not-valid-base64!!!", "mimeType": "audio/mp3"},
        ],
    }
    env, binaries, _full_text = mcp_result_to_envelope(result, "audio_tool")
    assert len(env.artifacts) == 1
    handle = "mcp:audio_tool:audio:0"
    assert handle in binaries
    raw, mime, _label = binaries[handle]
    assert raw == b"not-valid-base64!!!"
    assert mime == "audio/mp3"


# ---------------------------------------------------------------------------
# MCP adapter — resource_link content type
# ---------------------------------------------------------------------------


def test_mcp_result_to_envelope_resource_link() -> None:
    result = {
        "content": [
            {
                "type": "resource_link",
                "uri": "file:///data/report.csv",
                "mimeType": "text/csv",
                "name": "Monthly Report",
            },
        ],
    }
    env, binaries, _full_text = mcp_result_to_envelope(result, "reporter")
    assert len(env.artifacts) == 1
    ref = env.artifacts[0]
    assert ref.media_type == "text/csv"
    assert ref.label == "Monthly Report"
    assert ref.size_bytes == len(b"file:///data/report.csv")  # URI bytes length
    handle = "mcp:reporter:resource_link:0"
    assert handle in binaries
    raw, mime, _label = binaries[handle]
    assert raw == b"file:///data/report.csv"
    # Binaries MIME reflects the actual payload (a URI), not the linked resource.
    assert mime == "text/uri-list"


def test_mcp_result_to_envelope_resource_link_no_name() -> None:
    result = {
        "content": [
            {
                "type": "resource_link",
                "uri": "file:///data.json",
            },
        ],
    }
    env, _binaries, _full_text = mcp_result_to_envelope(result, "tool")
    assert env.artifacts[0].label == "file:///data.json"


# ---------------------------------------------------------------------------
# MCP adapter — structuredContent
# ---------------------------------------------------------------------------


def test_mcp_result_to_envelope_structured_content() -> None:
    structured = {"count": 42, "status": "done", "items": [1, 2, 3]}
    result: dict[str, object] = {
        "content": [{"type": "text", "text": "summary line"}],
        "structuredContent": structured,
    }
    env, binaries, full_text = mcp_result_to_envelope(result, "query")
    # Text content still present
    assert "summary line" in full_text
    # Structured content stored as artifact
    sc_handle = "mcp:query:structured_content"
    assert sc_handle in binaries
    raw, mime, _label = binaries[sc_handle]
    assert mime == "application/json"
    import json as _json

    parsed = _json.loads(raw)
    assert parsed["count"] == 42
    assert parsed["status"] == "done"
    # Facts extracted from top-level keys
    assert any("count: 42" in f for f in env.facts)
    assert any("status: done" in f for f in env.facts)
    # ArtifactRef is present
    assert any(a.handle == sc_handle for a in env.artifacts)


def test_mcp_result_to_envelope_structured_content_only() -> None:
    """structuredContent without content parts."""
    result: dict[str, object] = {
        "content": [],
        "structuredContent": {"key": "value"},
    }
    env, binaries, full_text = mcp_result_to_envelope(result, "tool")
    assert "mcp:tool:structured_content" in binaries
    # Facts from structured content appear in the text
    assert "key: value" in full_text


# ---------------------------------------------------------------------------
# MCP adapter — content-part annotations
# ---------------------------------------------------------------------------


def test_mcp_result_to_envelope_content_annotations() -> None:
    result = {
        "content": [
            {
                "type": "text",
                "text": "for humans only",
                "annotations": {"audience": ["human"], "priority": 0.9},
            },
            {
                "type": "text",
                "text": "for the model",
                "annotations": {"audience": ["assistant"], "priority": 0.5},
            },
        ],
    }
    env, _binaries, _full_text = mcp_result_to_envelope(result, "annotated")
    annotations = env.provenance.get("content_annotations")
    assert annotations is not None
    assert len(annotations) == 2
    assert annotations[0]["audience"] == ["human"]
    assert annotations[0]["priority"] == 0.9
    assert annotations[0]["part_index"] == 0
    assert annotations[1]["audience"] == ["assistant"]
    assert annotations[1]["part_index"] == 1


def test_mcp_result_to_envelope_no_annotations() -> None:
    result = {
        "content": [{"type": "text", "text": "plain"}],
    }
    env, _binaries, _full_text = mcp_result_to_envelope(result, "plain")
    assert "content_annotations" not in env.provenance


# ---------------------------------------------------------------------------
# MCP adapter — backward compat: existing text/image/resource still work
# ---------------------------------------------------------------------------


def test_mcp_result_to_envelope_mixed_old_and_new_types() -> None:
    """Verify text + image + audio + resource_link coexist correctly."""
    import base64

    png = b"\x89PNG"
    wav = b"RIFF"
    result = {
        "content": [
            {"type": "text", "text": "hello"},
            {"type": "image", "data": base64.b64encode(png).decode(), "mimeType": "image/png"},
            {"type": "audio", "data": base64.b64encode(wav).decode(), "mimeType": "audio/wav"},
            {
                "type": "resource_link",
                "uri": "file:///x",
                "mimeType": "text/plain",
                "name": "X",
            },
        ],
        "structuredContent": {"mixed": True},
    }
    env, binaries, full_text = mcp_result_to_envelope(result, "mix")
    assert "hello" in full_text
    # 4 artifacts: image + audio + resource_link + structured_content
    assert len(env.artifacts) == 4
    assert len(binaries) == 4


# ---------------------------------------------------------------------------
# SelectableItem — output_schema round-trip
# ---------------------------------------------------------------------------


def test_selectable_item_output_schema_round_trip() -> None:
    from contextweaver.types import SelectableItem

    schema = {"type": "object", "properties": {"x": {"type": "integer"}}}
    item = SelectableItem(
        id="t1",
        kind="tool",
        name="t1",
        description="test",
        output_schema=schema,
    )
    d = item.to_dict()
    assert d["output_schema"] == schema
    restored = SelectableItem.from_dict(d)
    assert restored.output_schema == schema


def test_selectable_item_output_schema_none_round_trip() -> None:
    from contextweaver.types import SelectableItem

    item = SelectableItem(id="t2", kind="tool", name="t2", description="test")
    d = item.to_dict()
    assert d["output_schema"] is None
    restored = SelectableItem.from_dict(d)
    assert restored.output_schema is None


def test_selectable_item_output_schema_empty_dict_round_trip() -> None:
    from contextweaver.types import SelectableItem

    item = SelectableItem(id="t3", kind="tool", name="t3", description="test", output_schema={})
    d = item.to_dict()
    assert d["output_schema"] == {}
    restored = SelectableItem.from_dict(d)
    assert restored.output_schema == {}


def test_mcp_tool_to_selectable_with_empty_output_schema() -> None:
    tool_def = {"name": "any_output", "description": "Accepts any output", "outputSchema": {}}
    item = mcp_tool_to_selectable(tool_def)
    assert item.output_schema == {}


# ---------------------------------------------------------------------------
# MCP adapter — load_mcp_session_jsonl
# ---------------------------------------------------------------------------


def test_load_mcp_session_jsonl() -> None:
    lines = [
        json.dumps({"id": "tc1", "type": "tool_call", "text": "call search"}),
        json.dumps({"id": "tr1", "type": "tool_result", "text": "42 rows", "parent_id": "tc1"}),
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


# ---------------------------------------------------------------------------
# JSONL loaders — field validation / coercion errors
# ---------------------------------------------------------------------------


def test_load_mcp_session_jsonl_non_dict_line() -> None:
    """A JSON array line should raise CatalogError, not AttributeError."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    ) as f:
        f.write("[1, 2, 3]\n")
        f.flush()
        path = f.name
    try:
        with pytest.raises(CatalogError, match="Expected JSON object"):
            load_mcp_session_jsonl(path)
    finally:
        os.unlink(path)


def test_load_mcp_session_jsonl_bad_token_estimate() -> None:
    """Non-numeric token_estimate should raise CatalogError, not ValueError."""
    line = json.dumps({"id": "x", "type": "user_turn", "text": "hi", "token_estimate": "abc"})
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    ) as f:
        f.write(line + "\n")
        f.flush()
        path = f.name
    try:
        with pytest.raises(CatalogError, match="Invalid context item at line 1"):
            load_mcp_session_jsonl(path)
    finally:
        os.unlink(path)


def test_load_mcp_session_jsonl_bad_metadata() -> None:
    """Non-dict metadata should raise CatalogError, not TypeError."""
    line = json.dumps({"id": "x", "type": "user_turn", "text": "hi", "metadata": "bad"})
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    ) as f:
        f.write(line + "\n")
        f.flush()
        path = f.name
    try:
        with pytest.raises(CatalogError, match="Invalid context item at line 1"):
            load_mcp_session_jsonl(path)
    finally:
        os.unlink(path)


def test_load_a2a_session_jsonl_non_dict_line() -> None:
    """A JSON array line should raise CatalogError, not AttributeError."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    ) as f:
        f.write('"just a string"\n')
        f.flush()
        path = f.name
    try:
        with pytest.raises(CatalogError, match="Expected JSON object"):
            load_a2a_session_jsonl(path)
    finally:
        os.unlink(path)


def test_load_a2a_session_jsonl_bad_token_estimate() -> None:
    """Non-numeric token_estimate should raise CatalogError."""
    line = json.dumps({"id": "x", "type": "agent_msg", "text": "hi", "token_estimate": [1]})
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    ) as f:
        f.write(line + "\n")
        f.flush()
        path = f.name
    try:
        with pytest.raises(CatalogError, match="Invalid context item at line 1"):
            load_a2a_session_jsonl(path)
    finally:
        os.unlink(path)


# ---------------------------------------------------------------------------
# FastMCP adapter — infer_fastmcp_namespace
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("tool_name", "expected"),
    [
        ("github.create_issue", "github"),
        ("filesystem/read", "filesystem"),
        ("github_search_repos", "github"),
        ("slack_send_message", "slack"),
        ("github_search", "github"),  # 2-segment → accepted by FastMCP heuristic
        ("read_file", "read"),  # 2-segment → first segment is namespace
        ("search", "fastmcp"),  # single word → fallback
        ("", "fastmcp"),
        (".hidden", "fastmcp"),
        ("/path", "fastmcp"),
    ],
)
def test_infer_fastmcp_namespace(tool_name: str, expected: str) -> None:
    assert infer_fastmcp_namespace(tool_name) == expected


# ---------------------------------------------------------------------------
# FastMCP adapter — fastmcp_tool_to_selectable
# ---------------------------------------------------------------------------


def test_fastmcp_tool_basic() -> None:
    tool_def = {
        "name": "github_search_repos",
        "description": "Search GitHub repositories",
    }
    item = fastmcp_tool_to_selectable(tool_def)
    assert item.id == "fastmcp:github_search_repos"
    assert item.kind == "tool"
    assert item.name == "search_repos"  # namespace prefix stripped
    assert item.namespace == "github"
    assert item.description == "Search GitHub repositories"
    assert "fastmcp" in item.tags
    assert "mcp" not in item.tags  # replaced by "fastmcp"


def test_fastmcp_tool_two_segment_namespace() -> None:
    tool_def = {
        "name": "weather_forecast",
        "description": "Get weather forecast",
    }
    item = fastmcp_tool_to_selectable(tool_def)
    assert item.namespace == "weather"
    assert item.name == "forecast"
    assert item.id == "fastmcp:weather_forecast"


def test_fastmcp_tool_single_word_fallback() -> None:
    tool_def = {"name": "search", "description": "Global search"}
    item = fastmcp_tool_to_selectable(tool_def)
    assert item.namespace == "fastmcp"
    assert item.name == "search"  # no prefix to strip


def test_fastmcp_tool_explicit_namespace() -> None:
    tool_def = {"name": "query", "description": "Run a query"}
    item = fastmcp_tool_to_selectable(tool_def, namespace="db")
    assert item.namespace == "db"
    assert item.name == "query"


def test_fastmcp_tool_tag_mapping() -> None:
    tool_def = {
        "name": "api_list_users",
        "description": "List users",
        "meta": {"tags": ["production", "admin"]},
    }
    item = fastmcp_tool_to_selectable(tool_def)
    assert "fastmcp" in item.tags
    assert "production" in item.tags
    assert "admin" in item.tags
    assert "mcp" not in item.tags


def test_fastmcp_tool_schema_preserved() -> None:
    schema = {"type": "object", "properties": {"q": {"type": "string"}}}
    tool_def = {
        "name": "db_query",
        "description": "Query data",
        "inputSchema": schema,
    }
    item = fastmcp_tool_to_selectable(tool_def)
    assert item.args_schema == schema


def test_fastmcp_tool_output_schema_preserved() -> None:
    out_schema = {"type": "object", "properties": {"result": {"type": "string"}}}
    tool_def = {
        "name": "db_query",
        "description": "Query data",
        "outputSchema": out_schema,
    }
    item = fastmcp_tool_to_selectable(tool_def)
    assert item.output_schema == out_schema


def test_fastmcp_tool_annotations() -> None:
    tool_def = {
        "name": "fs_read_file",
        "description": "Read a file",
        "annotations": {"readOnlyHint": True, "costHint": 0.05},
    }
    item = fastmcp_tool_to_selectable(tool_def)
    assert item.side_effects is False
    assert item.cost_hint == 0.05
    assert "read-only" in item.tags


def test_fastmcp_tool_destructive_hint() -> None:
    tool_def = {
        "name": "fs_delete_file",
        "description": "Delete a file",
        "annotations": {"destructiveHint": True},
    }
    item = fastmcp_tool_to_selectable(tool_def)
    assert "destructive" in item.tags
    assert item.side_effects is True


def test_fastmcp_tool_missing_name() -> None:
    with pytest.raises(CatalogError, match="missing required fields"):
        fastmcp_tool_to_selectable({"description": "no name"})


def test_fastmcp_tool_missing_description() -> None:
    with pytest.raises(CatalogError, match="missing required fields"):
        fastmcp_tool_to_selectable({"name": "tool"})


def test_fastmcp_tool_meta_merged() -> None:
    tool_def = {
        "name": "api_status",
        "description": "Check status",
        "meta": {"version": "1.2", "author": "team"},
    }
    item = fastmcp_tool_to_selectable(tool_def)
    assert item.metadata["version"] == "1.2"
    assert item.metadata["author"] == "team"


def test_fastmcp_tool_meta_set_normalized_to_list() -> None:
    """meta containing a set must be normalized so to_dict() / JSON serialization works."""
    import json

    tool_def = {
        "name": "api_status",
        "description": "Check status",
        "meta": {"tags": {"prod", "admin"}, "owners": ("alice", "bob")},
    }
    item = fastmcp_tool_to_selectable(tool_def)
    # Coerced to list — no set or tuple in metadata
    assert isinstance(item.metadata["tags"], list)
    assert isinstance(item.metadata["owners"], list)
    # to_dict() must not raise (JSON-serializable)
    assert json.dumps(item.to_dict())


def test_fastmcp_tool_dot_namespace_stripping() -> None:
    """Dot-delimited names: name field must not repeat the namespace prefix."""
    tool_def = {"name": "github.create_issue", "description": "Create an issue"}
    item = fastmcp_tool_to_selectable(tool_def)
    assert item.namespace == "github"
    assert item.name == "create_issue"
    assert item.id == "fastmcp:github.create_issue"


def test_fastmcp_tool_slash_namespace_stripping() -> None:
    """Slash-delimited names: name field must not repeat the namespace prefix."""
    tool_def = {"name": "filesystem/read", "description": "Read a file"}
    item = fastmcp_tool_to_selectable(tool_def)
    assert item.namespace == "filesystem"
    assert item.name == "read"
    assert item.id == "fastmcp:filesystem/read"


# ---------------------------------------------------------------------------
# FastMCP adapter — fastmcp_tools_to_catalog
# ---------------------------------------------------------------------------


def test_fastmcp_tools_to_catalog() -> None:
    tools = [
        {"name": "github_search_repos", "description": "Search repos"},
        {"name": "github_create_issue", "description": "Create issue"},
        {"name": "slack_send_message", "description": "Send message"},
    ]
    catalog = fastmcp_tools_to_catalog(tools)
    assert len(catalog.all()) == 3

    github_items = catalog.filter_by_namespace("github")
    assert len(github_items) == 2

    slack_items = catalog.filter_by_namespace("slack")
    assert len(slack_items) == 1


def test_fastmcp_tools_to_catalog_with_namespace_override() -> None:
    tools = [
        {"name": "search", "description": "Search"},
        {"name": "list", "description": "List"},
    ]
    catalog = fastmcp_tools_to_catalog(tools, namespace="myserver")
    assert all(item.namespace == "myserver" for item in catalog.all())


def test_fastmcp_tools_to_catalog_duplicate() -> None:
    tools = [
        {"name": "github_search", "description": "Search"},
        {"name": "github_search", "description": "Search again"},
    ]
    with pytest.raises(CatalogError, match="Duplicate item id"):
        fastmcp_tools_to_catalog(tools)


def test_fastmcp_tools_to_catalog_empty() -> None:
    catalog = fastmcp_tools_to_catalog([])
    assert len(catalog.all()) == 0


# ---------------------------------------------------------------------------
# FastMCP adapter — load_fastmcp_catalog
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_fastmcp_catalog_happy_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """load_fastmcp_catalog converts discovered tools into a populated Catalog."""
    import importlib
    import sys

    import contextweaver.adapters.fastmcp as fastmcp_mod

    class FakeTool:
        def model_dump(self, *, exclude_none: bool = False) -> dict:  # type: ignore[override]
            return {"name": "github_search", "description": "Search GitHub"}

    class FakeClient:
        def __init__(self, source: object) -> None: ...

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_: object) -> None: ...

        async def list_tools(self) -> list:
            return [FakeTool()]

    fake_fastmcp = type(sys)("fastmcp")
    fake_fastmcp.Client = FakeClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fastmcp", fake_fastmcp)
    importlib.reload(fastmcp_mod)

    catalog = await fastmcp_mod.load_fastmcp_catalog("fake://server")
    assert len(catalog.all()) == 1
    item = catalog.all()[0]
    assert item.id == "fastmcp:github_search"
    assert item.namespace == "github"
    assert item.name == "search"
    assert "fastmcp" in item.tags

    importlib.reload(fastmcp_mod)  # restore


@pytest.mark.asyncio
async def test_load_fastmcp_catalog_connection_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """Server-side errors are wrapped in CatalogError."""
    import importlib
    import sys

    import contextweaver.adapters.fastmcp as fastmcp_mod

    class BrokenClient:
        def __init__(self, source: object) -> None: ...

        async def __aenter__(self) -> BrokenClient:
            return self

        async def __aexit__(self, *_: object) -> None: ...

        async def list_tools(self) -> list:
            raise ConnectionRefusedError("offline")

    fake_fastmcp = type(sys)("fastmcp")
    fake_fastmcp.Client = BrokenClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fastmcp", fake_fastmcp)
    importlib.reload(fastmcp_mod)

    with pytest.raises(CatalogError, match="Failed to list tools"):
        await fastmcp_mod.load_fastmcp_catalog("fake://server")

    importlib.reload(fastmcp_mod)  # restore


@pytest.mark.asyncio
async def test_load_fastmcp_catalog_existing_client(monkeypatch: pytest.MonkeyPatch) -> None:
    """Passing an existing Client instance is used directly (no wrapping)."""
    import importlib
    import sys

    import contextweaver.adapters.fastmcp as fastmcp_mod

    class FakeTool:
        def model_dump(self, *, exclude_none: bool = False) -> dict:  # type: ignore[override]
            return {"name": "slack_notify", "description": "Send Slack notification"}

    class FakeClient:
        def __init__(self, source: object) -> None: ...

        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *_: object) -> None: ...

        async def list_tools(self) -> list:
            return [FakeTool()]

    fake_fastmcp = type(sys)("fastmcp")
    fake_fastmcp.Client = FakeClient  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "fastmcp", fake_fastmcp)
    importlib.reload(fastmcp_mod)

    # Pass a pre-built client instance — should not be double-wrapped.
    existing_client = FakeClient(None)
    catalog = await fastmcp_mod.load_fastmcp_catalog(existing_client)
    assert len(catalog.all()) == 1
    assert catalog.all()[0].namespace == "slack"

    importlib.reload(fastmcp_mod)  # restore


# ---------------------------------------------------------------------------
# FastMCP adapter — load_fastmcp_catalog (import guard)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_load_fastmcp_catalog_requires_extra(monkeypatch: pytest.MonkeyPatch) -> None:
    """Verify load_fastmcp_catalog raises CatalogError when fastmcp is missing."""
    import importlib
    import sys

    import contextweaver.adapters.fastmcp as fastmcp_mod

    # Hide the fastmcp package from import machinery.
    saved = sys.modules.get("fastmcp")
    monkeypatch.setitem(sys.modules, "fastmcp", None)  # type: ignore[arg-type]

    # Reload so the lazy import sees the blocked module.
    importlib.reload(fastmcp_mod)

    with pytest.raises(CatalogError, match="FastMCP is not installed"):
        await fastmcp_mod.load_fastmcp_catalog("http://localhost:9999/mcp")

    # Restore.
    if saved is not None:
        monkeypatch.setitem(sys.modules, "fastmcp", saved)
    else:
        monkeypatch.delitem(sys.modules, "fastmcp", raising=False)
    importlib.reload(fastmcp_mod)


# ===========================================================================
# weaver-spec contract adapter (issue #143)
#
# These tests exercise the round-trip mapping between contextweaver types and
# the canonical weaver-spec contracts.  The ``weaver_contracts`` package is
# installed via the ``[dev]`` extras, so the suite runs unconditionally in CI.
# ===========================================================================

weaver_contracts = pytest.importorskip("weaver_contracts")

from datetime import datetime, timezone  # noqa: E402

from contextweaver.adapters.weaver_contracts import (  # noqa: E402
    from_weaver_choice_card,
    from_weaver_choice_card_single,
    from_weaver_frame,
    from_weaver_routing_decision,
    from_weaver_selectable_item,
    to_weaver_choice_card,
    to_weaver_choice_cards,
    to_weaver_frame,
    to_weaver_routing_decision,
    to_weaver_selectable_item,
)
from contextweaver.envelope import (  # noqa: E402
    ChoiceCard,
    ResultEnvelope,
    RoutingDecision,
)
from contextweaver.types import ArtifactRef, SelectableItem, ViewSpec  # noqa: E402

# ---------------------------------------------------------------------------
# SelectableItem ↔ weaver_contracts.SelectableItem
# ---------------------------------------------------------------------------


def test_to_weaver_selectable_item_basic_fields() -> None:
    item = SelectableItem(
        id="t1", kind="tool", name="search", description="Search the DB", namespace="db"
    )
    spec = to_weaver_selectable_item(item)
    assert isinstance(spec, weaver_contracts.SelectableItem)
    assert spec.id == "t1"
    assert spec.label == "search"
    assert spec.description == "Search the DB"
    assert spec.capability_id == "db:search"


def test_to_weaver_selectable_item_no_namespace_uses_id_as_capability_id() -> None:
    item = SelectableItem(id="t1", kind="tool", name="search", description="d")
    spec = to_weaver_selectable_item(item)
    assert spec.capability_id == "t1"


def test_to_weaver_selectable_item_stashes_cw_extras_in_metadata() -> None:
    item = SelectableItem(
        id="t1",
        kind="agent",
        name="bot",
        description="d",
        tags=["nlp"],
        namespace="ai",
        args_schema={"type": "object"},
        output_schema={"type": "string"},
        examples=["hello"],
        constraints={"max_tokens": 100},
        side_effects=True,
        cost_hint=0.5,
        metadata={"foo": "bar"},
    )
    spec = to_weaver_selectable_item(item)
    assert spec.metadata["foo"] == "bar"
    cw = spec.metadata["_contextweaver"]
    assert cw["kind"] == "agent"
    assert cw["tags"] == ["nlp"]
    assert cw["namespace"] == "ai"
    assert cw["args_schema"] == {"type": "object"}
    assert cw["output_schema"] == {"type": "string"}
    assert cw["examples"] == ["hello"]
    assert cw["constraints"] == {"max_tokens": 100}
    assert cw["side_effects"] is True
    assert cw["cost_hint"] == 0.5


def test_selectable_item_roundtrip_lossless() -> None:
    item = SelectableItem(
        id="t1",
        kind="agent",
        name="bot",
        description="A chatbot",
        tags=["nlp", "ai"],
        namespace="ai",
        args_schema={"type": "object", "properties": {"q": {"type": "string"}}},
        output_schema={"type": "string"},
        examples=["greet"],
        constraints={"max_tokens": 100},
        side_effects=True,
        cost_hint=0.7,
        metadata={"trace_id": "abc"},
    )
    restored = from_weaver_selectable_item(to_weaver_selectable_item(item))
    assert restored == item


def test_selectable_item_roundtrip_defaults() -> None:
    item = SelectableItem(id="t1", kind="tool", name="n", description="d")
    restored = from_weaver_selectable_item(to_weaver_selectable_item(item))
    assert restored == item


def test_from_weaver_selectable_item_foreign_origin() -> None:
    spec = weaver_contracts.SelectableItem(
        id="ext-1",
        label="External tool",
        description="Came from agent-kernel",
        capability_id="agentkernel:do_thing",
        metadata={"external": True},
    )
    item = from_weaver_selectable_item(spec)
    assert item.id == "ext-1"
    assert item.kind == "tool"
    assert item.name == "External tool"
    assert item.namespace == "agentkernel"  # inferred from capability_id
    assert item.metadata == {"external": True}


# ---------------------------------------------------------------------------
# ChoiceCard ↔ weaver_contracts.ChoiceCard
# ---------------------------------------------------------------------------


def test_to_weaver_choice_card_wraps_single_card_as_menu() -> None:
    card = ChoiceCard(
        id="search",
        name="search",
        description="Search the DB",
        tags=["data"],
        kind="tool",
        namespace="db",
        has_schema=True,
        cost_hint=0.2,
        side_effects=False,
        score=0.95,
    )
    spec = to_weaver_choice_card(card)
    assert isinstance(spec, weaver_contracts.ChoiceCard)
    assert spec.id == "menu:search"
    assert len(spec.items) == 1
    option = spec.items[0]
    assert option.id == "search"
    assert option.label == "search"
    assert option.capability_id == "db:search"


def test_to_weaver_choice_card_custom_menu_id_and_hint() -> None:
    card = ChoiceCard(id="t1", name="n", description="d")
    spec = to_weaver_choice_card(card, menu_id="custom", context_hint="pick one")
    assert spec.id == "custom"
    assert spec.context_hint == "pick one"


def test_choice_card_roundtrip_via_single_helper_lossless() -> None:
    card = ChoiceCard(
        id="search",
        name="search_db",
        description="Search the DB",
        tags=["data", "query"],
        kind="tool",
        namespace="db",
        has_schema=True,
        cost_hint=0.4,
        side_effects=True,
        score=0.83,
    )
    restored = from_weaver_choice_card_single(to_weaver_choice_card(card))
    assert restored == card


def test_choice_card_roundtrip_score_none() -> None:
    card = ChoiceCard(id="t1", name="n", description="d", score=None)
    restored = from_weaver_choice_card_single(to_weaver_choice_card(card))
    assert restored.score is None
    assert restored == card


def test_from_weaver_choice_card_returns_list() -> None:
    cards = [
        ChoiceCard(id="a", name="a", description="A"),
        ChoiceCard(id="b", name="b", description="B"),
        ChoiceCard(id="c", name="c", description="C"),
    ]
    menu = to_weaver_choice_cards(cards, menu_id="menu-1")
    restored = from_weaver_choice_card(menu)
    assert len(restored) == 3
    assert [c.id for c in restored] == ["a", "b", "c"]
    assert restored == cards


def test_to_weaver_choice_cards_empty_raises() -> None:
    with pytest.raises(CatalogError, match="at least one item"):
        to_weaver_choice_cards([], menu_id="m")


def test_from_weaver_choice_card_single_rejects_multi() -> None:
    cards = [ChoiceCard(id="a", name="a", description="A") for _ in range(2)]
    menu = to_weaver_choice_cards(cards, menu_id="m")
    with pytest.raises(CatalogError, match="single-item"):
        from_weaver_choice_card_single(menu)


# ---------------------------------------------------------------------------
# RoutingDecision ↔ weaver_contracts.RoutingDecision
# ---------------------------------------------------------------------------


def test_routing_decision_roundtrip_lossless() -> None:
    cards = [
        ChoiceCard(id="t1", name="search", description="Search", score=0.9),
        ChoiceCard(id="t2", name="filter", description="Filter", score=0.7, tags=["q"]),
    ]
    ts = datetime(2026, 5, 14, 10, 0, 0, tzinfo=timezone.utc)
    rd = RoutingDecision(
        id="rd-1",
        choice_cards=cards,
        timestamp=ts,
        selected_item_id="t1",
        selected_card_id="t1",
        context_summary="searching for reports",
        metadata={"trace_id": "abc"},
    )
    restored = from_weaver_routing_decision(to_weaver_routing_decision(rd))
    assert restored == rd


def test_routing_decision_to_weaver_groups_into_single_menu() -> None:
    cards = [ChoiceCard(id=f"t{i}", name=f"n{i}", description="d") for i in range(3)]
    rd = RoutingDecision(
        id="rd-1",
        choice_cards=cards,
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    spec = to_weaver_routing_decision(rd)
    assert isinstance(spec, weaver_contracts.RoutingDecision)
    assert len(spec.choice_cards) == 1
    assert spec.choice_cards[0].id == "rd-1:menu"
    assert len(spec.choice_cards[0].items) == 3


def test_routing_decision_empty_choice_cards_raises() -> None:
    rd = RoutingDecision(
        id="rd-1",
        choice_cards=[],
        timestamp=datetime.now(timezone.utc),
    )
    with pytest.raises(CatalogError, match="at least one ChoiceCard"):
        to_weaver_routing_decision(rd)


def test_from_weaver_routing_decision_flattens_multiple_menus() -> None:
    # Build a spec decision with TWO menus directly to verify flattening.
    cards_a = [ChoiceCard(id="a1", name="a1", description="A1")]
    cards_b = [
        ChoiceCard(id="b1", name="b1", description="B1"),
        ChoiceCard(id="b2", name="b2", description="B2"),
    ]
    menu_a = to_weaver_choice_cards(cards_a, menu_id="menu-a")
    menu_b = to_weaver_choice_cards(cards_b, menu_id="menu-b")
    spec_rd = weaver_contracts.RoutingDecision(
        id="rd-multi",
        choice_cards=[menu_a, menu_b],
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    rd = from_weaver_routing_decision(spec_rd)
    assert [c.id for c in rd.choice_cards] == ["a1", "b1", "b2"]


def test_to_weaver_routing_decision_validates_against_spec_post_init() -> None:
    # Spec dataclass validates required fields; verify our adapter produces
    # something that passes.
    cards = [ChoiceCard(id="t1", name="n", description="d")]
    rd = RoutingDecision(
        id="rd-1",
        choice_cards=cards,
        timestamp=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    # Should not raise — spec __post_init__ accepts.
    spec = to_weaver_routing_decision(rd)
    assert spec.id == "rd-1"


# ---------------------------------------------------------------------------
# ResultEnvelope ↔ weaver_contracts.Frame
# ---------------------------------------------------------------------------


def test_to_weaver_frame_basic_fields() -> None:
    env = ResultEnvelope(status="ok", summary="Query returned 5 rows")
    when = datetime(2026, 5, 14, 12, 0, 0, tzinfo=timezone.utc)
    frame = to_weaver_frame(env, frame_id="f-1", capability_id="db:search", created_at=when)
    assert isinstance(frame, weaver_contracts.Frame)
    assert frame.frame_id == "f-1"
    assert frame.capability_id == "db:search"
    assert frame.summary == "Query returned 5 rows"
    assert frame.created_at == when


def test_to_weaver_frame_handles_empty_summary() -> None:
    env = ResultEnvelope(status="ok", summary="")
    frame = to_weaver_frame(env, frame_id="f-1", capability_id="cap")
    # Frame.summary post_init rejects empty strings.
    assert frame.summary == "(no summary)"


def test_to_weaver_frame_default_created_at_is_aware() -> None:
    env = ResultEnvelope(status="ok", summary="done")
    frame = to_weaver_frame(env, frame_id="f-1", capability_id="cap")
    assert frame.created_at.tzinfo is not None


def test_to_weaver_frame_handle_refs_from_artifacts() -> None:
    refs = [
        ArtifactRef(handle="h1", media_type="application/json", size_bytes=12, label="a"),
        ArtifactRef(handle="h2", media_type="text/plain", size_bytes=99),
    ]
    env = ResultEnvelope(status="ok", summary="s", artifacts=refs)
    frame = to_weaver_frame(env, frame_id="f-1", capability_id="cap")
    assert frame.handle_refs == ["h1", "h2"]


def test_frame_roundtrip_lossless() -> None:
    refs = [ArtifactRef(handle="h1", media_type="application/json", size_bytes=42, label="r")]
    views = [ViewSpec(view_id="v1", label="rows", selector={"start": 0, "end": 10})]
    env = ResultEnvelope(
        status="partial",
        summary="3/5 rows",
        facts=["count: 3", "status: warning"],
        artifacts=refs,
        views=views,
        provenance={"tool": "db.search", "redaction_notes": "ssn masked"},
    )
    when = datetime(2026, 5, 14, 12, 30, 0, tzinfo=timezone.utc)
    frame = to_weaver_frame(env, frame_id="f-1", capability_id="db:search", created_at=when)
    restored = from_weaver_frame(frame)
    assert restored == env


def test_to_weaver_frame_lifts_redaction_notes_from_provenance() -> None:
    env = ResultEnvelope(
        status="ok",
        summary="s",
        provenance={"redaction_notes": "PII removed from rows 3-5"},
    )
    frame = to_weaver_frame(env, frame_id="f-1", capability_id="cap")
    assert frame.redaction_notes == "PII removed from rows 3-5"


def test_from_weaver_frame_foreign_origin_falls_back_to_defaults() -> None:
    # A Frame produced outside CW (no _contextweaver metadata key).
    when = datetime(2026, 5, 14, tzinfo=timezone.utc)
    frame = weaver_contracts.Frame(
        frame_id="f-ext",
        capability_id="kernel:fetch",
        summary="External summary",
        created_at=when,
        structured_data=None,
        handle_refs=["h-ext-1"],
        redaction_notes="redacted by kernel",
        metadata={"origin": "agent-kernel"},
    )
    env = from_weaver_frame(frame)
    assert env.status == "ok"  # default
    assert env.summary == "External summary"
    assert env.facts == []
    assert env.views == []
    # Stub ArtifactRef constructed from handle_refs.
    assert len(env.artifacts) == 1
    assert env.artifacts[0].handle == "h-ext-1"
    assert env.artifacts[0].media_type == "application/octet-stream"
    assert env.provenance == {"redaction_notes": "redacted by kernel"}


def test_from_weaver_frame_invalid_status_defaults_to_ok() -> None:
    when = datetime(2026, 5, 14, tzinfo=timezone.utc)
    frame = weaver_contracts.Frame(
        frame_id="f-1",
        capability_id="cap",
        summary="s",
        created_at=when,
        structured_data={"status": "invalid_value", "facts": [], "views": []},
    )
    env = from_weaver_frame(frame)
    assert env.status == "ok"


# ---------------------------------------------------------------------------
# weaver_contracts adapter — import guard
# ---------------------------------------------------------------------------


def test_weaver_adapter_raises_when_module_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """When weaver_contracts is not importable, public functions raise CatalogError."""
    import sys

    saved = sys.modules.get("weaver_contracts")
    monkeypatch.setitem(sys.modules, "weaver_contracts", None)  # type: ignore[arg-type]

    item = SelectableItem(id="t1", kind="tool", name="n", description="d")
    with pytest.raises(CatalogError, match="weaver_contracts is not installed"):
        to_weaver_selectable_item(item)

    if saved is not None:
        monkeypatch.setitem(sys.modules, "weaver_contracts", saved)
    else:
        monkeypatch.delitem(sys.modules, "weaver_contracts", raising=False)


# ---------------------------------------------------------------------------
# weaver_contracts adapter — preserves unknown spec metadata keys
# ---------------------------------------------------------------------------


def test_to_weaver_selectable_item_does_not_clobber_user_metadata() -> None:
    item = SelectableItem(
        id="t1",
        kind="tool",
        name="n",
        description="d",
        metadata={"user_key": 42, "another": [1, 2]},
    )
    spec = to_weaver_selectable_item(item)
    assert spec.metadata["user_key"] == 42
    assert spec.metadata["another"] == [1, 2]
    assert "_contextweaver" in spec.metadata
    # Round-trip preserves both user and CW metadata.
    restored = from_weaver_selectable_item(spec)
    assert restored.metadata == {"user_key": 42, "another": [1, 2]}

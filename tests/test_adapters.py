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

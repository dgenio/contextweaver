"""Tests for contextweaver.context.views."""

from __future__ import annotations

import json

from contextweaver.context.views import (
    ViewRegistry,
    _binary_views,
    _csv_views,
    _detect_content_type,
    _json_views,
    _looks_like_csv,
    _text_views,
    drilldown_tool_spec,
    generate_views,
)
from contextweaver.types import ArtifactRef, SelectableItem, ViewSpec


def _ref(
    handle: str = "artifact:test",
    media_type: str = "text/plain",
    size_bytes: int = 100,
) -> ArtifactRef:
    return ArtifactRef(handle=handle, media_type=media_type, size_bytes=size_bytes)


# ------------------------------------------------------------------
# JSON view generation
# ------------------------------------------------------------------


def test_json_views_dict_keys() -> None:
    ref = _ref(media_type="application/json")
    data = json.dumps({"name": "Alice", "age": 30, "role": "admin"}).encode()
    views = _json_views(ref, data)
    # Should have: 1 all-keys view + 3 individual key views
    assert len(views) == 4
    all_keys = views[0]
    assert "json_keys" in all_keys.view_id
    assert all_keys.selector["type"] == "json_keys"
    assert sorted(all_keys.selector["keys"]) == ["age", "name", "role"]


def test_json_views_large_dict_includes_head() -> None:
    ref = _ref(media_type="application/json", size_bytes=500)
    obj = {f"key_{i}": f"value_{i}" for i in range(20)}
    data = json.dumps(obj).encode()
    views = _json_views(ref, data)
    # 1 all-keys + 10 individual keys (capped) + 1 head view
    assert any(v.view_id.endswith(":head") for v in views)


def test_json_views_array() -> None:
    ref = _ref(media_type="application/json")
    data = json.dumps([{"id": i} for i in range(50)]).encode()
    views = _json_views(ref, data)
    assert any("array_head" in v.view_id for v in views)


def test_json_views_empty_dict() -> None:
    ref = _ref(media_type="application/json")
    data = b"{}"
    views = _json_views(ref, data)
    assert len(views) == 0


def test_json_views_invalid_json() -> None:
    ref = _ref(media_type="application/json")
    data = b"not json at all"
    views = _json_views(ref, data)
    assert len(views) == 0


# ------------------------------------------------------------------
# CSV view generation
# ------------------------------------------------------------------


def test_csv_views_basic() -> None:
    ref = _ref(media_type="text/csv")
    lines = ["name,age,role"] + [f"user{i},{20 + i},admin" for i in range(20)]
    data = "\n".join(lines).encode()
    views = _csv_views(ref, data)
    assert len(views) == 2  # head + tail
    head = views[0]
    assert head.selector["type"] == "rows"
    assert head.selector["start"] == 0
    assert head.selector["end"] == 10


def test_csv_views_short() -> None:
    ref = _ref(media_type="text/csv")
    data = b"name,age\nAlice,30\nBob,25"
    views = _csv_views(ref, data)
    assert len(views) == 1  # only head, no tail


def test_csv_views_empty() -> None:
    ref = _ref(media_type="text/csv")
    data = b""
    views = _csv_views(ref, data)
    assert len(views) == 0


# ------------------------------------------------------------------
# Text view generation
# ------------------------------------------------------------------


def test_text_views_short() -> None:
    ref = _ref(media_type="text/plain")
    data = b"line1\nline2\nline3"
    views = _text_views(ref, data)
    assert len(views) == 1  # only head
    assert views[0].selector["type"] == "lines"


def test_text_views_long() -> None:
    ref = _ref(media_type="text/plain")
    lines = [f"line {i}" for i in range(50)]
    data = "\n".join(lines).encode()
    views = _text_views(ref, data)
    assert len(views) == 2  # head + tail
    head, tail = views[0], views[1]
    assert head.selector["start"] == 0
    assert head.selector["end"] == 20
    assert tail.selector["start"] == 30
    assert tail.selector["end"] == 50


def test_text_views_empty() -> None:
    ref = _ref(media_type="text/plain")
    data = b""
    views = _text_views(ref, data)
    # Empty text has zero lines after splitlines
    assert len(views) == 0


# ------------------------------------------------------------------
# Binary view generation
# ------------------------------------------------------------------


def test_binary_views() -> None:
    ref = _ref(media_type="image/png", size_bytes=2048)
    data = b"\x89PNG\r\n\x1a\n" + b"\x00" * 100
    views = _binary_views(ref, data)
    assert len(views) == 1
    assert "Header" in views[0].label
    assert "image/png" in views[0].label
    assert views[0].selector == {"type": "head", "chars": 128}


# ------------------------------------------------------------------
# Content-type detection
# ------------------------------------------------------------------


def test_detect_json_from_octet_stream() -> None:
    data = json.dumps({"key": "value"}).encode()
    assert _detect_content_type(data, "application/octet-stream") == "application/json"


def test_detect_csv_from_octet_stream() -> None:
    data = b"name,age,role\nAlice,30,admin\nBob,25,user"
    assert _detect_content_type(data, "application/octet-stream") == "text/csv"


def test_detect_plain_text_from_octet_stream() -> None:
    data = b"just some plain text without any structure"
    assert _detect_content_type(data, "application/octet-stream") == "text/plain"


def test_detect_image_passthrough() -> None:
    data = b"\x89PNG\r\n\x1a\n"
    assert _detect_content_type(data, "image/png") == "image/png"


def test_detect_explicit_json() -> None:
    data = b'{"key": "value"}'
    assert _detect_content_type(data, "application/json") == "application/json"


def test_detect_binary_content() -> None:
    data = bytes(range(256))
    assert _detect_content_type(data, "application/octet-stream") == "application/octet-stream"


# ------------------------------------------------------------------
# CSV heuristic
# ------------------------------------------------------------------


def test_looks_like_csv_positive() -> None:
    text = "name,age,role\nAlice,30,admin\nBob,25,user"
    assert _looks_like_csv(text) is True


def test_looks_like_csv_single_line() -> None:
    assert _looks_like_csv("just one line") is False


def test_looks_like_csv_plain_text() -> None:
    text = "abc\ndef"
    # Single words per line — no delimiter for csv.Sniffer to detect
    assert _looks_like_csv(text) is False


# ------------------------------------------------------------------
# ViewRegistry
# ------------------------------------------------------------------


def test_registry_defaults() -> None:
    reg = ViewRegistry()
    ref = _ref(media_type="application/json")
    data = json.dumps({"a": 1}).encode()
    views = reg.generate_views(ref, data)
    assert len(views) > 0
    assert all(isinstance(v, ViewSpec) for v in views)


def test_registry_custom_generator() -> None:
    reg = ViewRegistry()

    def my_generator(ref: ArtifactRef, data: bytes) -> list[ViewSpec]:
        return [
            ViewSpec(
                view_id=f"{ref.handle}:custom",
                label="Custom view",
                selector={"type": "head", "chars": 100},
                artifact_ref=ref,
            )
        ]

    reg.register("application/xml", my_generator)
    ref = _ref(media_type="application/xml")
    views = reg.generate_views(ref, b"<root/>")
    assert len(views) == 1
    assert views[0].label == "Custom view"


def test_registry_override_builtin() -> None:
    reg = ViewRegistry()

    def override(ref: ArtifactRef, data: bytes) -> list[ViewSpec]:
        return [ViewSpec(view_id="override", label="Overridden")]

    reg.register("application/json", override)
    ref = _ref(media_type="application/json")
    views = reg.generate_views(ref, b'{"a": 1}')
    assert len(views) == 1
    assert views[0].label == "Overridden"


def test_registry_fallback_to_binary() -> None:
    reg = ViewRegistry()
    ref = _ref(media_type="application/x-custom-binary")
    views = reg.generate_views(ref, b"\x00\x01\x02")
    assert len(views) == 1
    assert "Header" in views[0].label


def test_registry_prefix_match() -> None:
    """Text-like MIME types should fall back to text generator via prefix match."""
    reg = ViewRegistry()
    ref = _ref(media_type="text/markdown")
    data = "\n".join(f"line {i}" for i in range(30)).encode()
    views = reg.generate_views(ref, data)
    assert len(views) >= 1
    assert views[0].selector["type"] == "lines"


# ------------------------------------------------------------------
# generate_views() convenience function
# ------------------------------------------------------------------


def test_generate_views_default_registry() -> None:
    ref = _ref(media_type="text/plain")
    data = b"hello\nworld\nfoo"
    views = generate_views(ref, data)
    assert len(views) >= 1


def test_generate_views_custom_registry() -> None:
    reg = ViewRegistry()
    reg.register("text/plain", lambda r, d: [ViewSpec(view_id="x", label="X")])
    ref = _ref(media_type="text/plain")
    views = generate_views(ref, b"data", registry=reg)
    assert len(views) == 1
    assert views[0].label == "X"


# ------------------------------------------------------------------
# ViewSpec serialisation round-trip
# ------------------------------------------------------------------


def test_viewspec_round_trip() -> None:
    ref = _ref()
    vs = ViewSpec(
        view_id="v1",
        label="Test",
        selector={"type": "head", "chars": 100},
        artifact_ref=ref,
    )
    d = vs.to_dict()
    restored = ViewSpec.from_dict(d)
    assert restored.view_id == vs.view_id
    assert restored.label == vs.label
    assert restored.selector == vs.selector
    assert restored.artifact_ref is not None
    assert restored.artifact_ref.handle == ref.handle


# ------------------------------------------------------------------
# drilldown_tool_spec
# ------------------------------------------------------------------


def test_drilldown_tool_spec_returns_selectable_item() -> None:
    spec = drilldown_tool_spec()
    assert isinstance(spec, SelectableItem)
    assert spec.kind == "internal"
    assert spec.name == "drilldown"
    assert "handle" in spec.args_schema.get("properties", {})
    assert "selector" in spec.args_schema.get("properties", {})


def test_drilldown_tool_spec_deterministic() -> None:
    s1 = drilldown_tool_spec()
    s2 = drilldown_tool_spec()
    assert s1.id == s2.id
    assert s1.to_dict() == s2.to_dict()


def test_drilldown_tool_spec_tags() -> None:
    spec = drilldown_tool_spec()
    assert "drilldown" in spec.tags
    assert "progressive-disclosure" in spec.tags

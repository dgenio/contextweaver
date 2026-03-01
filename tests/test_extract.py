"""Tests for contextweaver.summarize.extract."""

from __future__ import annotations

import json

from contextweaver.summarize.extract import (
    StructuredExtractor,
    extract_bullet_list,
    extract_facts,
    extract_key_value_pairs,
    extract_numbered_list,
)


def test_extract_key_value_pairs_colon() -> None:
    text = "status: ok\ncount: 42"
    kv = extract_key_value_pairs(text)
    assert kv["status"] == "ok"
    assert kv["count"] == "42"


def test_extract_key_value_pairs_equals() -> None:
    text = "name = Alice\nage = 30"
    kv = extract_key_value_pairs(text)
    assert kv["name"] == "Alice"


def test_extract_numbered_list() -> None:
    text = "1. first item\n2. second item\n3. third item"
    items = extract_numbered_list(text)
    assert items == ["first item", "second item", "third item"]


def test_extract_bullet_list_dash() -> None:
    text = "- alpha\n- beta\n- gamma"
    items = extract_bullet_list(text)
    assert items == ["alpha", "beta", "gamma"]


def test_extract_bullet_list_star() -> None:
    text = "* one\n* two"
    items = extract_bullet_list(text)
    assert items == ["one", "two"]


def test_extract_facts_combined() -> None:
    text = "status: ok\n1. first\n- bullet"
    facts = extract_facts(text, {})
    assert any("status" in f for f in facts)
    assert "first" in facts
    assert "bullet" in facts


def test_extract_facts_deduplication() -> None:
    text = "status: ok\nstatus: ok"
    facts = extract_facts(text, {})
    count = sum(1 for f in facts if f == "status: ok")
    assert count == 1


# ---------------------------------------------------------------------------
# StructuredExtractor
# ---------------------------------------------------------------------------


def test_extractor_empty() -> None:
    ext = StructuredExtractor()
    assert ext.extract("", {}) == []
    assert ext.extract("   ", {}) == []


def test_extractor_json_object() -> None:
    ext = StructuredExtractor()
    obj = {"name": "Alice", "scores": [90, 85], "meta": {"role": "admin"}}
    raw = json.dumps(obj)
    facts = ext.extract(raw, {})
    assert any("object" in f for f in facts)
    assert any("name" in f for f in facts)
    assert any("scores" in f for f in facts)


def test_extractor_json_array() -> None:
    ext = StructuredExtractor()
    arr = [
        {"id": 1, "name": "a", "value": 10},
        {"id": 2, "name": "b", "value": 20},
        {"id": 3, "name": "c", "value": 30},
    ]
    raw = json.dumps(arr)
    facts = ext.extract(raw, {})
    assert any("array" in f for f in facts)
    assert any("3 items" in f for f in facts)
    assert any("columns" in f for f in facts)


def test_extractor_plain_text() -> None:
    ext = StructuredExtractor()
    text = "Line one\nLine two\nLine three"
    facts = ext.extract(text, {})
    assert any("line_count" in f for f in facts)


def test_extractor_detects_emails() -> None:
    ext = StructuredExtractor()
    text = "Contact us at alice@example.com or bob@test.org for details."
    facts = ext.extract(text, {})
    assert any("emails" in f for f in facts)


def test_extractor_detects_urls() -> None:
    ext = StructuredExtractor()
    text = "Visit https://example.com and https://docs.test.org/api for more."
    facts = ext.extract(text, {})
    assert any("urls" in f for f in facts)


def test_extractor_detects_numbers() -> None:
    ext = StructuredExtractor()
    text = "Total: 42\nAverage: 3.14\nCount: 100"
    facts = ext.extract(text, {})
    assert any("numbers" in f for f in facts)


def test_extractor_max_chars_truncation() -> None:
    ext = StructuredExtractor(max_chars=50)
    obj = {f"key_{i}": f"value_{i}" for i in range(50)}
    raw = json.dumps(obj)
    facts = ext.extract(raw, {})
    total_len = sum(len(f) for f in facts)
    assert total_len <= 50


def test_extractor_key_value_in_plain_text() -> None:
    ext = StructuredExtractor()
    text = "status: ok\nresult: success\ncount: 42"
    facts = ext.extract(text, {})
    assert any("status" in f for f in facts)

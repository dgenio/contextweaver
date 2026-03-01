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


# -- StructuredExtractor tests ------------------------------------------------


def test_extractor_json_object() -> None:
    ex = StructuredExtractor()
    obj = {"name": "Alice", "age": 30, "scores": [1, 2, 3]}
    facts = ex.extract(json.dumps(obj), {})
    assert any("object" in f for f in facts)
    assert any("name" in f for f in facts)


def test_extractor_json_array_of_dicts() -> None:
    ex = StructuredExtractor()
    arr = [
        {"name": "Alice", "age": 30},
        {"name": "Bob", "age": 25},
        {"name": "Carol", "age": 28},
    ]
    facts = ex.extract(json.dumps(arr), {})
    assert any("array" in f for f in facts)
    assert any("columns" in f for f in facts)


def test_extractor_json_simple_array() -> None:
    ex = StructuredExtractor()
    arr = [1, 2, 3]
    facts = ex.extract(json.dumps(arr), {})
    assert any("3 items" in f for f in facts)


def test_extractor_plain_text_line_count() -> None:
    ex = StructuredExtractor()
    text = "line 1\nline 2\nline 3"
    facts = ex.extract(text, {})
    assert any("line_count: 3" in f for f in facts)


def test_extractor_detects_emails() -> None:
    ex = StructuredExtractor()
    text = "Contact alice@example.com or bob@test.org"
    facts = ex.extract(text, {})
    assert any("emails" in f for f in facts)


def test_extractor_detects_urls() -> None:
    ex = StructuredExtractor()
    text = "Visit https://example.com for info"
    facts = ex.extract(text, {})
    assert any("urls" in f for f in facts)


def test_extractor_detects_numbers() -> None:
    ex = StructuredExtractor()
    text = "Total: 1,234 items processed. Cost: 56.78"
    facts = ex.extract(text, {})
    assert any("numbers" in f for f in facts)


def test_extractor_detects_headings() -> None:
    ex = StructuredExtractor()
    text = "# Introduction\nSome text\n# Results\nMore text"
    facts = ex.extract(text, {})
    assert any("sections" in f for f in facts)


def test_extractor_bounded_output() -> None:
    ex = StructuredExtractor(max_chars=50)
    obj = {f"key_{i}": f"value_{i}" for i in range(100)}
    facts = ex.extract(json.dumps(obj), {})
    total = sum(len(f) for f in facts)
    assert total <= 50


def test_extractor_via_media_type() -> None:
    ex = StructuredExtractor()
    obj = {"key": "val"}
    facts = ex.extract(
        json.dumps(obj),
        {"media_type": "application/json"},
    )
    assert any("object" in f for f in facts)


def test_extractor_implements_protocol() -> None:
    from contextweaver.protocols import Extractor

    ex = StructuredExtractor()
    assert isinstance(ex, Extractor)

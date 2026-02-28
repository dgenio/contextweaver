"""Tests for contextweaver.summarize.extract."""

from __future__ import annotations

from contextweaver.summarize.extract import (
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

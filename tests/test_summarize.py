"""Tests for contextweaver.summarize.rules."""

from __future__ import annotations

import json

from contextweaver.summarize.rules import RuleBasedSummarizer, RuleEngine, SummarizationRule


def test_rule_matches_media_type() -> None:
    rule = SummarizationRule(media_type_prefix="text/", summarizer_name="text_sum")
    assert rule.matches("text/plain", {})
    assert not rule.matches("application/json", {})


def test_rule_matches_tags() -> None:
    rule = SummarizationRule(required_tags=["json"], summarizer_name="json_sum")
    assert rule.matches("anything", {"tags": ["json", "other"]})
    assert not rule.matches("anything", {"tags": ["xml"]})


def test_rule_empty_matches_all() -> None:
    rule = SummarizationRule(summarizer_name="default")
    assert rule.matches("anything/here", {})


def test_engine_first_match_wins() -> None:
    engine = RuleEngine(
        [
            SummarizationRule(media_type_prefix="text/", summarizer_name="text_sum", priority=10),
            SummarizationRule(summarizer_name="default", priority=0),
        ]
    )
    assert engine.resolve("text/plain", {}) == "text_sum"
    assert engine.resolve("application/json", {}) == "default"


def test_engine_no_match_returns_default() -> None:
    engine = RuleEngine()
    assert engine.resolve("image/png", {}) == "default"


def test_engine_add_rule() -> None:
    engine = RuleEngine()
    engine.add_rule(SummarizationRule(media_type_prefix="image/", summarizer_name="img"))
    assert engine.resolve("image/png", {}) == "img"


# ---------------------------------------------------------------------------
# RuleBasedSummarizer
# ---------------------------------------------------------------------------


def test_summarizer_empty_text() -> None:
    s = RuleBasedSummarizer()
    assert s.summarize("", {}) == "(empty)"
    assert s.summarize("   ", {}) == "(empty)"


def test_summarizer_short_text() -> None:
    s = RuleBasedSummarizer()
    result = s.summarize("short text", {})
    assert result == "short text"


def test_summarizer_json_object() -> None:
    s = RuleBasedSummarizer()
    obj = {"name": "Alice", "age": 30, "active": True}
    raw = json.dumps(obj)
    result = s.summarize(raw, {})
    assert "JSON object" in result
    assert "3 key(s)" in result
    assert "name" in result


def test_summarizer_json_array() -> None:
    s = RuleBasedSummarizer()
    arr = [{"id": 1, "value": "x"}, {"id": 2, "value": "y"}]
    raw = json.dumps(arr)
    result = s.summarize(raw, {})
    assert "JSON array" in result
    assert "2 item(s)" in result


def test_summarizer_key_lines() -> None:
    s = RuleBasedSummarizer()
    text = "line 1\nstatus: error detected\nline 3\ntotal: 42\nmore text"
    result = s.summarize(text, {})
    assert "error" in result.lower() or "total" in result.lower()


def test_summarizer_head_tail_truncation() -> None:
    s = RuleBasedSummarizer(max_chars=60)
    text = "a" * 200
    result = s.summarize(text, {})
    assert len(result) <= 60
    assert "[...truncated...]" in result


def test_summarizer_max_chars_respected() -> None:
    s = RuleBasedSummarizer(max_chars=100)
    obj = {f"key_{i}": f"value_{i}" for i in range(50)}
    raw = json.dumps(obj)
    result = s.summarize(raw, {})
    assert len(result) <= 100


def test_summarizer_all_rules() -> None:
    engine = RuleEngine()
    rules = engine.all_rules()
    assert isinstance(rules, list)

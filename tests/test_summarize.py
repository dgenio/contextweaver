"""Tests for contextweaver.summarize.rules."""

from __future__ import annotations

import json

from contextweaver.summarize.rules import (
    RuleBasedSummarizer,
    RuleEngine,
    SummarizationRule,
)


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


# -- RuleBasedSummarizer tests -----------------------------------------------


def test_summarizer_plain_text_short() -> None:
    s = RuleBasedSummarizer()
    result = s.summarize("hello world", {})
    assert "hello world" in result


def test_summarizer_plain_text_long() -> None:
    s = RuleBasedSummarizer(max_chars=100)
    text = "\n".join(f"line {i}" for i in range(50))
    result = s.summarize(text, {})
    assert len(result) <= 100
    assert "[...truncated...]" in result


def test_summarizer_plain_text_with_key_lines() -> None:
    s = RuleBasedSummarizer()
    text = "status: ok\nsome noise here\nerror count: 5\nmore noise"
    result = s.summarize(text, {})
    assert "error" in result.lower() or "status" in result.lower()


def test_summarizer_json_object() -> None:
    s = RuleBasedSummarizer()
    obj = {"name": "Alice", "age": 30, "city": "NYC"}
    result = s.summarize(json.dumps(obj), {})
    assert "JSON object" in result
    assert "keys" in result


def test_summarizer_json_object_via_media_type() -> None:
    s = RuleBasedSummarizer()
    obj = {"key": "val"}
    result = s.summarize(
        json.dumps(obj),
        {"media_type": "application/json"},
    )
    assert "JSON object" in result


def test_summarizer_json_array() -> None:
    s = RuleBasedSummarizer()
    arr = [{"name": "Alice"}, {"name": "Bob"}]
    result = s.summarize(json.dumps(arr), {})
    assert "JSON array" in result
    assert "2" in result


def test_summarizer_json_simple_array() -> None:
    s = RuleBasedSummarizer()
    arr = [1, 2, 3, 4, 5]
    result = s.summarize(json.dumps(arr), {})
    assert "JSON array" in result
    assert "5 items" in result


def test_summarizer_empty_text() -> None:
    s = RuleBasedSummarizer()
    assert s.summarize("", {}) == "(empty)"


def test_summarizer_implements_protocol() -> None:
    from contextweaver.protocols import Summarizer

    s = RuleBasedSummarizer()
    assert isinstance(s, Summarizer)

"""Tests for contextweaver.summarize.rules."""

from __future__ import annotations

from contextweaver.summarize.rules import RuleEngine, SummarizationRule


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

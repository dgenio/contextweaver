"""Tests for contextweaver.summarize.rules -- RuleBasedSummarizer with plain text, JSON, key lines."""

from __future__ import annotations

import json

from contextweaver.summarize.rules import RuleBasedSummarizer


class TestRuleBasedSummarizer:
    """Tests for the RuleBasedSummarizer class."""

    def test_short_text_returned_as_is(self) -> None:
        summarizer = RuleBasedSummarizer(max_chars=300)
        text = "Short text here."
        result = summarizer.summarize(text)
        assert result == text

    def test_long_text_truncated(self) -> None:
        summarizer = RuleBasedSummarizer(max_chars=100)
        text = "\n".join(f"Line {i}: Some content here." for i in range(50))
        result = summarizer.summarize(text)
        assert len(result) <= 100

    def test_json_object_summary(self) -> None:
        summarizer = RuleBasedSummarizer()
        data = json.dumps({"name": "Alice", "age": 30, "items": [1, 2, 3]})
        result = summarizer.summarize(data)
        assert "JSON object" in result
        assert "keys" in result.lower() or "name" in result

    def test_json_array_summary(self) -> None:
        summarizer = RuleBasedSummarizer()
        data = json.dumps([{"id": 1}, {"id": 2}, {"id": 3}])
        result = summarizer.summarize(data)
        assert "JSON array" in result
        assert "3 items" in result

    def test_json_empty_array(self) -> None:
        summarizer = RuleBasedSummarizer()
        data = json.dumps([])
        result = summarizer.summarize(data)
        assert "Empty JSON array" in result

    def test_text_with_key_lines(self) -> None:
        summarizer = RuleBasedSummarizer(max_chars=200)
        text = "\n".join(
            [
                "Header info",
                "status: ok",
                "total: 42",
                "some padding line " * 10,
                "more padding " * 10,
                "error: none found",
            ]
        )
        result = summarizer.summarize(text)
        assert len(result) <= 200

    def test_max_chars_override(self) -> None:
        summarizer = RuleBasedSummarizer(max_chars=1000)
        text = "A" * 500
        result = summarizer.summarize(text, max_chars=50)
        assert len(result) <= 50

    def test_empty_text(self) -> None:
        summarizer = RuleBasedSummarizer()
        result = summarizer.summarize("")
        assert result == ""

    def test_truncation_marker(self) -> None:
        summarizer = RuleBasedSummarizer(max_chars=200)
        text = "\n".join(f"Line {i}" for i in range(100))
        result = summarizer.summarize(text)
        assert "[...truncated...]" in result

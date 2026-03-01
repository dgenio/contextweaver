"""Tests for contextweaver.summarize.extract -- StructuredExtractor with JSON/table/text."""

from __future__ import annotations

import json

from contextweaver.summarize.extract import StructuredExtractor


class TestStructuredExtractor:
    """Tests for the StructuredExtractor class."""

    def test_extract_json_object(self) -> None:
        extractor = StructuredExtractor()
        data = json.dumps({"name": "Alice", "age": 30, "items": [1, 2, 3]})
        result = extractor.extract(data)
        assert result["type"] == "json_object"
        assert "name" in result["keys"]
        assert result["key_count"] == 3

    def test_extract_json_object_with_arrays(self) -> None:
        extractor = StructuredExtractor()
        data = json.dumps({"users": [1, 2, 3], "orders": [4, 5]})
        result = extractor.extract(data)
        assert result["type"] == "json_object"
        assert "array_fields" in result
        assert result["array_fields"]["users"]["length"] == 3

    def test_extract_json_array_table(self) -> None:
        extractor = StructuredExtractor()
        data = json.dumps(
            [
                {"id": 1, "name": "Alice"},
                {"id": 2, "name": "Bob"},
                {"id": 3, "name": "Charlie"},
            ]
        )
        result = extractor.extract(data)
        assert result["type"] == "table"
        assert result["row_count"] == 3
        assert "columns" in result
        assert "head" in result
        assert len(result["head"]) == 3

    def test_extract_json_array_simple(self) -> None:
        extractor = StructuredExtractor()
        data = json.dumps([1, 2, 3, 4, 5])
        result = extractor.extract(data)
        assert result["type"] == "table"
        assert result["row_count"] == 5

    def test_extract_plain_text(self) -> None:
        extractor = StructuredExtractor()
        text = "SUMMARY:\nThis is a report.\nTotal count: 42\nContact: user@example.com"
        result = extractor.extract(text)
        assert result["type"] == "text"
        assert result["line_count"] == 4

    def test_extract_text_with_headings(self) -> None:
        extractor = StructuredExtractor()
        text = "# Title\nSome content\n## Section:\nMore content"
        result = extractor.extract(text)
        assert result["type"] == "text"
        assert "headings" in result
        assert any("#" in h for h in result["headings"])

    def test_extract_text_with_entities(self) -> None:
        extractor = StructuredExtractor()
        text = "Contact alice@example.com or visit https://example.com for $1,234.56"
        result = extractor.extract(text)
        assert "entities" in result
        assert "emails" in result["entities"]
        assert "urls" in result["entities"]
        assert "numbers" in result["entities"]

    def test_extract_with_media_type(self) -> None:
        extractor = StructuredExtractor()
        result = extractor.extract("plain text", media_type="text/plain")
        assert result["type"] == "text"

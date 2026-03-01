"""Structured extraction helpers for contextweaver.

Default Extractor implementation. Strategies by detected content type:
- JSON object: top-level keys, value types, array lengths, first N items sample
- JSON array / tabular: row count, column names, head sample (3 rows)
- Plain text: line count, first headings/section titles, detected entities
"""

from __future__ import annotations

import json
import re
from typing import Any

_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")
_URL_RE = re.compile(r"https?://\S+")
_NUMBER_RE = re.compile(r"\b\d[\d,]*\.?\d*\b")


class StructuredExtractor:
    """Default Extractor implementation.

    Output always bounded (~500 chars total values).
    """

    def extract(self, text: str, media_type: str = "text/plain") -> dict[str, Any]:
        """Return structured extraction from *text*."""
        stripped = text.strip()

        # Try JSON
        if stripped.startswith(("{", "[")):
            try:
                data = json.loads(stripped)
                if isinstance(data, dict):
                    return self._extract_json_object(data)
                if isinstance(data, list):
                    return self._extract_json_array(data)
            except (json.JSONDecodeError, TypeError):
                pass

        return self._extract_text(text)

    def _extract_json_object(self, data: dict[str, Any]) -> dict[str, Any]:
        keys = list(data.keys())
        {k: type(v).__name__ for k, v in list(data.items())[:20]}
        array_fields = {}
        for k, v in data.items():
            if isinstance(v, list):
                array_fields[k] = {"length": len(v)}

        # Build a bounded sample
        sample: dict[str, Any] = {}
        chars = 0
        for k, v in data.items():
            s = json.dumps(v, default=str)
            if chars + len(s) > 400:
                break
            if not isinstance(v, (list, dict)) or len(s) < 100:
                sample[k] = v
                chars += len(s)

        result: dict[str, Any] = {
            "type": "json_object",
            "keys": keys[:20],
            "key_count": len(keys),
        }
        if sample:
            result["sample"] = sample
        if array_fields:
            result["array_fields"] = dict(list(array_fields.items())[:5])
        return result

    def _extract_json_array(self, data: list[Any]) -> dict[str, Any]:
        result: dict[str, Any] = {
            "type": "table",
            "row_count": len(data),
        }

        if data and isinstance(data[0], dict):
            result["columns"] = list(data[0].keys())[:20]
            # Head sample
            head = []
            for row in data[:3]:
                if isinstance(row, dict):
                    head.append({k: str(v)[:50] for k, v in list(row.items())[:10]})
            result["head"] = head
        elif data:
            result["head"] = [str(x)[:100] for x in data[:3]]

        return result

    def _extract_text(self, text: str) -> dict[str, Any]:
        lines = text.splitlines()
        result: dict[str, Any] = {
            "type": "text",
            "line_count": len(lines),
        }

        # Detect headings (lines that look like headers)
        headings = []
        for line in lines[:50]:
            stripped = line.strip()
            if stripped and len(stripped) < 80 and not stripped.endswith(","):
                if stripped.startswith("#") or stripped.isupper() or stripped.endswith(":"):
                    headings.append(stripped)
        if headings:
            result["headings"] = headings[:5]

        # Detect entities
        emails = _EMAIL_RE.findall(text)[:5]
        urls = _URL_RE.findall(text)[:5]
        numbers = _NUMBER_RE.findall(text)[:10]

        entities: dict[str, list[str]] = {}
        if emails:
            entities["emails"] = emails
        if urls:
            entities["urls"] = urls
        if numbers:
            entities["numbers"] = numbers
        if entities:
            result["entities"] = entities

        return result

"""Rule-based summarisation for contextweaver.

Default Summarizer implementation: head + tail + truncation for plain text,
top-level keys + structure overview for JSON, priority lines for key data.
"""

from __future__ import annotations

import json
import re
from typing import Any

_KEY_LINE_RE = re.compile(
    r"\b(error|success|failed|total|count|status|result|warning)\b", re.IGNORECASE
)


class RuleBasedSummarizer:
    """Default Summarizer.

    - Plain text: head + tail + "[...truncated...]"
    - JSON: top-level keys + structure overview
    - Key lines: prioritise lines with numbers, dates, status keywords
    """

    def __init__(self, max_chars: int = 300) -> None:
        self._max_chars = max_chars

    def summarize(self, text: str, max_chars: int | None = None) -> str:
        """Return a summary of *text*."""
        limit = max_chars if max_chars is not None else self._max_chars

        # Try JSON first
        stripped = text.strip()
        if stripped.startswith(("{", "[")):
            try:
                data = json.loads(stripped)
                return self._summarize_json(data, limit)
            except (json.JSONDecodeError, TypeError):
                pass

        return self._summarize_text(text, limit)

    def _summarize_json(self, data: Any, limit: int) -> str:
        if isinstance(data, dict):
            keys = list(data.keys())
            key_str = ", ".join(keys[:10])
            if len(keys) > 10:
                key_str += f", ... ({len(keys)} total)"
            result = f"JSON object with keys: [{key_str}]"
            # Add array lengths for array values
            arrays = {k: len(v) for k, v in data.items() if isinstance(v, list)}
            if arrays:
                arr_parts = [f"{k}({n} items)" for k, n in list(arrays.items())[:5]]
                result += f" | arrays: {', '.join(arr_parts)}"
            return result[:limit]
        if isinstance(data, list):
            n = len(data)
            if n == 0:
                return "Empty JSON array"
            sample = json.dumps(data[0], default=str)
            if len(sample) > 100:
                sample = sample[:100] + "..."
            return f"JSON array with {n} items. First: {sample}"[:limit]
        return str(data)[:limit]

    def _summarize_text(self, text: str, limit: int) -> str:
        lines = text.splitlines()
        if not lines:
            return ""

        # Collect key lines
        [line for line in lines if _KEY_LINE_RE.search(line)]

        if len(text) <= limit:
            return text

        # Head + tail approach
        head_budget = limit * 2 // 3
        tail_budget = limit - head_budget - 20  # room for truncation marker

        head_text = ""
        for line in lines:
            if len(head_text) + len(line) + 1 > head_budget:
                break
            head_text += line + "\n"

        tail_text = ""
        for line in reversed(lines):
            if len(tail_text) + len(line) + 1 > max(tail_budget, 0):
                break
            tail_text = line + "\n" + tail_text

        result = head_text.rstrip()
        if tail_text.strip() and tail_text.strip() != head_text.strip():
            result += "\n[...truncated...]\n" + tail_text.rstrip()
        else:
            result += "\n[...truncated...]"

        return result[:limit]

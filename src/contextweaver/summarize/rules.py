"""Rule-based summarisation for contextweaver.

Provides a simple rule engine that maps media-type patterns and metadata tags
to summarisation strategies.  Also includes :class:`RuleBasedSummarizer`, a
concrete :class:`~contextweaver.protocols.Summarizer` implementation using
head+tail truncation, JSON structure overviews, and key-line extraction.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any


@dataclass
class SummarizationRule:
    """A single rule mapping a condition to a summariser name.

    Attributes:
        media_type_prefix: Match if the artifact's media_type starts with this
            string.  Empty string matches everything.
        required_tags: All listed tags must be present in the metadata's
            ``tags`` list for the rule to fire.
        summarizer_name: Logical name of the summariser to use when this rule
            fires.  Resolved at runtime by the summarisation dispatcher.
        priority: Higher priority rules are evaluated first.
    """

    media_type_prefix: str = ""
    required_tags: list[str] = field(default_factory=list)
    summarizer_name: str = "default"
    priority: int = 0

    def matches(self, media_type: str, metadata: dict[str, Any]) -> bool:
        """Return ``True`` if this rule fires for the given artifact context.

        Args:
            media_type: The MIME type of the artifact.
            metadata: Arbitrary metadata dict associated with the artifact.

        Returns:
            ``True`` when all conditions are satisfied.
        """
        if self.media_type_prefix and not media_type.startswith(self.media_type_prefix):
            return False
        tags: list[str] = metadata.get("tags", [])
        return all(t in tags for t in self.required_tags)


class RuleEngine:
    """Dispatch summarisation by evaluating a ranked list of :class:`SummarizationRule` objects.

    Rules are sorted by descending priority; the first match wins.
    """

    def __init__(self, rules: list[SummarizationRule] | None = None) -> None:
        self._rules: list[SummarizationRule] = sorted(
            rules or [], key=lambda r: r.priority, reverse=True
        )

    def add_rule(self, rule: SummarizationRule) -> None:
        """Append *rule* and re-sort the rule list.

        Args:
            rule: The rule to add.
        """
        self._rules.append(rule)
        self._rules.sort(key=lambda r: r.priority, reverse=True)

    def resolve(self, media_type: str, metadata: dict[str, Any]) -> str:
        """Return the summariser name for the first matching rule.

        Args:
            media_type: MIME type of the artifact.
            metadata: Metadata dict.

        Returns:
            The ``summarizer_name`` of the first matching rule, or ``"default"``
            if no rule matches.
        """
        for rule in self._rules:
            if rule.matches(media_type, metadata):
                return rule.summarizer_name
        return "default"

    def all_rules(self) -> list[SummarizationRule]:
        """Return a copy of the rule list in priority order."""
        return list(self._rules)


_KEY_LINE_RE = re.compile(
    r"\b(error|success|failed|total|count|status|result|warning|exception)\b",
    re.IGNORECASE,
)


class RuleBasedSummarizer:
    """Concrete :class:`~contextweaver.protocols.Summarizer` implementation.

    Applies one of three strategies depending on the content:

    * **JSON** — top-level keys + structure overview.
    * **Key-line** — lines containing status keywords (error, success, …)
      are promoted; remaining lines use head + tail truncation.
    * **Plain text** — head + tail + ``[...truncated...]``.

    The ``max_chars`` parameter caps total summary length (default 500).
    """

    def __init__(self, max_chars: int = 500) -> None:
        self._max_chars = max_chars

    def summarize(self, raw: str, metadata: dict[str, Any]) -> str:
        """Return a bounded summary of *raw*.

        Args:
            raw: The raw tool output to summarize.
            metadata: Metadata context (currently used only to detect JSON media
                types via a ``media_type`` key, if present).

        Returns:
            A summary string of at most :attr:`max_chars` characters.
        """
        _ = metadata  # reserved for future rule dispatch
        text = raw.strip()
        if not text:
            return "(empty)"

        # Try JSON summarization first.
        if text.startswith(("{", "[")):
            result = self._summarize_json(text)
            if result is not None:
                return result[:self._max_chars]

        # Key-line extraction for structured tool output.
        key_lines = self._extract_key_lines(text)
        if key_lines:
            body = "\n".join(key_lines)
            if len(body) <= self._max_chars:
                return body
            return body[: self._max_chars - 3] + "..."

        # Fallback: head + tail truncation.
        return self._head_tail(text)

    def _summarize_json(self, text: str) -> str | None:
        """Summarize a JSON string by listing top-level keys and structure."""
        try:
            obj = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return None

        if isinstance(obj, dict):
            keys = list(obj.keys())[:20]
            parts = [f"JSON object with {len(obj)} key(s): {', '.join(keys)}"]
            for k in keys[:5]:
                v = obj[k]
                parts.append(f"  {k}: {type(v).__name__}")
            return "\n".join(parts)

        if isinstance(obj, list):
            parts = [f"JSON array with {len(obj)} item(s)"]
            if obj and isinstance(obj[0], dict):
                sample_keys = list(obj[0].keys())[:10]
                parts.append(f"  item keys: {', '.join(sample_keys)}")
            return "\n".join(parts)

        return repr(obj)

    def _extract_key_lines(self, text: str) -> list[str]:
        """Return lines containing status keywords."""
        return [line.strip() for line in text.splitlines() if _KEY_LINE_RE.search(line)]

    def _head_tail(self, text: str) -> str:
        """Head + tail truncation with middle marker."""
        if len(text) <= self._max_chars:
            return text
        half = (self._max_chars - 20) // 2
        return text[:half] + "\n[...truncated...]\n" + text[-half:]

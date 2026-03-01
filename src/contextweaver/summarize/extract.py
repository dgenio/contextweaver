"""Structured extraction helpers for contextweaver.

Extracts facts, key-value pairs, and other structured information from raw
tool output strings.  Used by the context firewall to populate
:class:`~contextweaver.types.ResultEnvelope` instances.

Also provides :class:`StructuredExtractor`, a concrete
:class:`~contextweaver.protocols.Extractor` implementation.
"""

from __future__ import annotations

import json
import re
from typing import Any

# FUTURE: LLM-backed extractor for richer entity/relation extraction.


def extract_key_value_pairs(text: str) -> dict[str, str]:
    """Extract ``key: value`` or ``key = value`` pairs from *text*.

    Lines that do not match either pattern are ignored.

    Args:
        text: Raw text to scan.

    Returns:
        A dict mapping lowercased keys to their string values (whitespace
        stripped).  Later occurrences of the same key overwrite earlier ones.
    """
    result: dict[str, str] = {}
    pattern = re.compile(r"^\s*([A-Za-z_][A-Za-z0-9_ ]*?)\s*[:=]\s*(.*?)\s*$")
    for line in text.splitlines():
        match = pattern.match(line)
        if match:
            key = match.group(1).strip().lower().replace(" ", "_")
            value = match.group(2).strip()
            result[key] = value
    return result


def extract_numbered_list(text: str) -> list[str]:
    """Extract items from a numbered list (``1. item``, ``2. item``, …).

    Args:
        text: Raw text to scan.

    Returns:
        An ordered list of item strings (whitespace stripped).
    """
    pattern = re.compile(r"^\s*\d+[.)]\s+(.*?)\s*$")
    items = []
    for line in text.splitlines():
        match = pattern.match(line)
        if match:
            items.append(match.group(1))
    return items


def extract_bullet_list(text: str) -> list[str]:
    """Extract items from a bullet list (``-``, ``*``, or ``•`` prefixed lines).

    Args:
        text: Raw text to scan.

    Returns:
        An ordered list of item strings (whitespace stripped).
    """
    pattern = re.compile(r"^\s*[-*•]\s+(.*?)\s*$")
    items = []
    for line in text.splitlines():
        match = pattern.match(line)
        if match:
            items.append(match.group(1))
    return items


def extract_facts(text: str, metadata: dict[str, Any]) -> list[str]:
    """Extract a list of fact strings from *text*.

    Combines :func:`extract_key_value_pairs`, :func:`extract_numbered_list`,
    and :func:`extract_bullet_list` into a unified fact list.

    Args:
        text: Raw tool output to extract from.
        metadata: Metadata dict (currently unused; reserved for future rules).

    Returns:
        A deduplicated list of fact strings in discovery order.
    """
    _ = metadata  # reserved
    seen: set[str] = set()
    facts: list[str] = []

    for key, value in extract_key_value_pairs(text).items():
        fact = f"{key}: {value}"
        if fact not in seen:
            seen.add(fact)
            facts.append(fact)

    for item in extract_numbered_list(text):
        if item not in seen:
            seen.add(item)
            facts.append(item)

    for item in extract_bullet_list(text):
        if item not in seen:
            seen.add(item)
            facts.append(item)

    return facts


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
_URL_RE = re.compile(r"https?://[^\s<>\"']+")
# NOTE: Intentionally broad — matches version strings, IP fragments, etc.
# Acceptable because extracted numbers are display-only (fact summaries).
_NUMBER_RE = re.compile(r"\b\d[\d,.]*\b")


class StructuredExtractor:
    """Concrete :class:`~contextweaver.protocols.Extractor` implementation.

    Detects the content format and extracts structured facts accordingly:

    * **JSON object** — top-level keys, value types, array lengths, first N
      items sample.
    * **JSON array / tabular** — row count, column names, head sample (3 rows).
    * **Plain text** — line count, headings/sections, detected entities
      (emails, URLs, numbers).

    Output is bounded to ~500 chars total via *max_chars*.
    """

    def __init__(self, max_chars: int = 500) -> None:
        self._max_chars = max_chars

    def extract(self, raw: str, metadata: dict[str, Any]) -> list[str]:
        """Return a list of fact strings extracted from *raw*.

        Args:
            raw: The raw tool output to extract from.
            metadata: Context metadata (reserved for future use).

        Returns:
            A deduplicated list of fact strings, total length bounded by
            *max_chars*.
        """
        _ = metadata
        text = raw.strip()
        if not text:
            return []

        # Try JSON-based extraction first.
        if text.startswith(("{", "[")):
            result = self._extract_json(text)
            if result is not None:
                return self._truncate(result)

        # Fallback: plain-text extraction.
        return self._truncate(self._extract_plain(text))

    def _extract_json(self, text: str) -> list[str] | None:
        """Extract facts from a JSON string."""
        try:
            obj = json.loads(text)
        except (json.JSONDecodeError, ValueError):
            return None

        facts: list[str] = []
        if isinstance(obj, dict):
            facts.append(f"type: object ({len(obj)} keys)")
            for k in list(obj.keys())[:10]:
                v = obj[k]
                if isinstance(v, list):
                    facts.append(f"{k}: array[{len(v)}]")
                elif isinstance(v, dict):
                    facts.append(f"{k}: object({len(v)} keys)")
                else:
                    facts.append(f"{k}: {type(v).__name__} = {str(v)[:60]}")
        elif isinstance(obj, list):
            facts.append(f"type: array ({len(obj)} items)")
            if obj and isinstance(obj[0], dict):
                cols = list(obj[0].keys())
                facts.append(f"columns: {', '.join(cols[:10])}")
                for row in obj[:3]:
                    vals = [f"{k}={str(row.get(k, ''))[:20]}" for k in cols[:5]]
                    facts.append(f"  row: {', '.join(vals)}")
        else:
            facts.append(f"type: {type(obj).__name__} = {str(obj)[:80]}")
        return facts

    def _extract_plain(self, text: str) -> list[str]:
        """Extract facts from plain text."""
        facts: list[str] = []
        lines = text.splitlines()
        facts.append(f"line_count: {len(lines)}")

        # Detect headings (lines that are all-caps or followed by underlines).
        headings = [
            ln.strip()
            for ln in lines
            if ln.strip() and (ln.strip().isupper() or ln.strip().startswith("#"))
        ]
        if headings:
            facts.append(f"sections: {', '.join(headings[:5])}")

        # Detect entities.
        emails = _EMAIL_RE.findall(text)
        if emails:
            facts.append(f"emails: {', '.join(sorted(set(emails))[:5])}")

        urls = _URL_RE.findall(text)
        if urls:
            facts.append(f"urls: {', '.join(sorted(set(urls))[:5])}")

        numbers = _NUMBER_RE.findall(text)
        if numbers:
            unique = sorted(set(numbers))[:10]
            facts.append(f"numbers: {', '.join(unique)}")

        # Also include key-value pairs from the unified extractor.
        for key, value in extract_key_value_pairs(text).items():
            facts.append(f"{key}: {value}")

        return facts

    def _truncate(self, facts: list[str]) -> list[str]:
        """Trim the fact list so total chars stay under *max_chars*."""
        result: list[str] = []
        total = 0
        for fact in facts:
            if total + len(fact) > self._max_chars:
                break
            result.append(fact)
            total += len(fact)
        return result

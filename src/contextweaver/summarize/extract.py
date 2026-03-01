"""Structured extraction helpers for contextweaver.

Extracts facts, key-value pairs, and other structured information from raw
tool output strings.  Used by the context firewall to populate
:class:`~contextweaver.types.ResultEnvelope` instances.

Also provides :class:`StructuredExtractor`, the default
:class:`~contextweaver.protocols.Extractor` implementation.
"""

from __future__ import annotations

import json
import re
from typing import Any


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


# ---------------------------------------------------------------------------
# Entity detection patterns
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Z|a-z]{2,}")
_URL_RE = re.compile(r"https?://[^\s<>\"']+")
_NUMBER_RE = re.compile(r"\b\d[\d,.]+\b")


class StructuredExtractor:
    """Default :class:`~contextweaver.protocols.Extractor` implementation.

    Strategies:

    - **JSON object**: top-level keys, value types, array lengths, first N
      items sample.
    - **JSON array / tabular**: row_count, column names, head sample (3 rows).
    - **Plain text**: line_count, headings/sections, detected entities
      (emails, URLs, numbers).

    Output is bounded to approximately *max_chars* total (~500 by default).

    # FUTURE: LLM labeler/extractor for richer structured data.
    """

    def __init__(self, max_chars: int = 500) -> None:
        self._max_chars = max_chars

    def extract(self, raw: str, metadata: dict[str, Any]) -> list[str]:
        """Extract structured fact strings from *raw*.

        Args:
            raw: Raw tool output.
            metadata: Optional metadata (e.g. ``media_type``).

        Returns:
            A list of fact strings bounded by *max_chars* total.
        """
        media = str(metadata.get("media_type", ""))
        if "json" in media or self._looks_like_json(raw):
            return self._extract_json(raw)
        return self._extract_text(raw)

    # -- JSON extraction ---------------------------------------------------

    @staticmethod
    def _looks_like_json(text: str) -> bool:
        stripped = text.strip()
        return (
            stripped.startswith("{") and stripped.endswith("}")
        ) or (stripped.startswith("[") and stripped.endswith("]"))

    def _extract_json(self, raw: str) -> list[str]:
        try:
            obj = json.loads(raw)
        except (json.JSONDecodeError, ValueError):
            return self._extract_text(raw)

        facts: list[str] = []

        if isinstance(obj, dict):
            facts.append(f"type: object ({len(obj)} keys)")
            for key in sorted(obj.keys())[:15]:
                val = obj[key]
                vtype = type(val).__name__
                if isinstance(val, list):
                    facts.append(f"key {key}: list[{len(val)}]")
                elif isinstance(val, dict):
                    facts.append(f"key {key}: object")
                else:
                    short = str(val)[:60]
                    facts.append(f"key {key}: {vtype} = {short}")

        elif isinstance(obj, list):
            facts.append(f"type: array ({len(obj)} items)")
            if obj and isinstance(obj[0], dict):
                cols = sorted(obj[0].keys())
                facts.append(f"columns: {', '.join(cols[:15])}")
                for row in obj[:3]:
                    sample = json.dumps(row, sort_keys=True)
                    facts.append(f"row: {sample[:80]}")
            else:
                for item in obj[:3]:
                    facts.append(f"item: {str(item)[:80]}")
        else:
            facts.append(f"value: {str(obj)[:120]}")

        return self._trim(facts)

    # -- plain-text extraction ---------------------------------------------

    def _extract_text(self, raw: str) -> list[str]:
        lines = raw.splitlines()
        facts: list[str] = [f"line_count: {len(lines)}"]

        # Headings / sections (lines that are ALL CAPS or start with #)
        headings: list[str] = []
        for line in lines:
            stripped = line.strip()
            if stripped.startswith("#"):
                headings.append(stripped.lstrip("# "))
            elif (
                stripped
                and stripped == stripped.upper()
                and len(stripped) > 3
                and stripped[0].isalpha()
            ):
                headings.append(stripped)
        if headings:
            facts.append(
                f"sections: {', '.join(headings[:10])}"
            )

        # Detected entities
        emails = sorted(set(_EMAIL_RE.findall(raw)))
        urls = sorted(set(_URL_RE.findall(raw)))
        numbers = sorted(set(_NUMBER_RE.findall(raw)))

        if emails:
            facts.append(f"emails: {', '.join(emails[:5])}")
        if urls:
            facts.append(f"urls: {', '.join(urls[:5])}")
        if numbers:
            facts.append(f"numbers: {', '.join(numbers[:10])}")

        return self._trim(facts)

    # -- bounded output ----------------------------------------------------

    def _trim(self, facts: list[str]) -> list[str]:
        """Keep facts until *max_chars* budget is exhausted."""
        result: list[str] = []
        total = 0
        for f in facts:
            if total + len(f) > self._max_chars:
                break
            result.append(f)
            total += len(f)
        return result

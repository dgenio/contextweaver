"""Structured extraction helpers for contextweaver.

Extracts facts, key-value pairs, and other structured information from raw
tool output strings.  Used by the context firewall to populate
:class:`~contextweaver.types.ResultEnvelope` instances.
"""

from __future__ import annotations

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

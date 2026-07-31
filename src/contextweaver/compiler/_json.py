"""Deterministic JSON helpers for compiler artifacts."""

from __future__ import annotations

import hashlib
import json


def canonical_json_bytes(value: object) -> bytes:
    """Return a stable UTF-8 JSON encoding for *value*."""
    return json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def pretty_json(value: object) -> str:
    """Return deterministic, human-readable JSON with a trailing newline."""
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n"


def sha256_hex(data: bytes) -> str:
    """Return the SHA-256 hex digest for *data*."""
    return hashlib.sha256(data).hexdigest()


def digest_json(value: object) -> str:
    """Return the SHA-256 digest of *value*'s canonical JSON encoding."""
    return sha256_hex(canonical_json_bytes(value))

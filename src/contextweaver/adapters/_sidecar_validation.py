"""Stateless parsing + field-validation helpers for the HTTP sidecar.

Extracted from :mod:`contextweaver.adapters.sidecar` and
:mod:`contextweaver.adapters.sidecar_contract` so both stay within the
≤300-line module convention (issue #456).  Pure and dependency-free: request
body decoding, bearer-token extraction, and the typed contract-field coercions
that raise :class:`~contextweaver.exceptions.ConfigError` on malformed input.
Imports no HTTP machinery.  Not public API.
"""

from __future__ import annotations

import json
from typing import Any

from contextweaver.exceptions import ConfigError


def require_str(payload: dict[str, Any], key: str) -> str:
    """Return ``payload[key]`` as a non-empty ``str`` or raise ``ConfigError``."""
    value = payload.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"sidecar request field {key!r} must be a non-empty string")
    return value


def opt_int(payload: dict[str, Any], key: str, default: int) -> int:
    """Return ``payload[key]`` coerced to ``int`` (default when absent/null)."""
    value = payload.get(key, default)
    if value is None:
        return default
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"sidecar request field {key!r} must be an integer")
    return int(value)


def opt_str_list(payload: dict[str, Any], key: str) -> list[str]:
    """Return ``payload[key]`` as a ``list[str]`` (empty when absent/null)."""
    value = payload.get(key)
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ConfigError(f"sidecar request field {key!r} must be a list of strings")
    return list(value)


def bearer_token(header_value: str) -> str | None:
    """Extract the token from an ``Authorization: Bearer <token>`` header."""
    parts = header_value.split(None, 1)
    if len(parts) == 2 and parts[0].lower() == "bearer":
        return parts[1].strip()
    return None


def parse_json_object(body: bytes) -> dict[str, Any]:
    """Decode *body* as a JSON object, raising ``ConfigError`` otherwise."""
    if not body:
        raise ConfigError("request body is empty; expected a JSON object")
    try:
        parsed = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ConfigError(f"request body is not valid UTF-8 JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise ConfigError("request body must be a JSON object")
    return parsed

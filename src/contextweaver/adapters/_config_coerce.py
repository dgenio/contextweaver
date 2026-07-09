"""Shared config-value coercion helpers for the gateway upstream/startup/artifact
config modules (:mod:`upstream_config`, :mod:`startup_policy`,
:mod:`artifact_policy`).

Extracted so the three config modules stay within the ≤300-line ceiling
without duplicating the same coercion logic three times. Not public API.
"""

from __future__ import annotations

import os
import re
from typing import Any

from contextweaver.exceptions import ConfigError

_ENV_VAR_RE = re.compile(r"\$\{env:([A-Za-z_][A-Za-z0-9_]*)\}")
_TRUE_STRINGS: frozenset[str] = frozenset({"true", "1", "yes", "on"})
_FALSE_STRINGS: frozenset[str] = frozenset({"false", "0", "no", "off"})


def interpolate_env(value: str) -> str:
    """Replace every ``${env:VAR}`` placeholder in *value* with its env value.

    Raises:
        ConfigError: If a referenced variable is not set. Silently
            substituting an empty string would turn a missing credential
            into a confusing downstream connection failure instead of a
            clear config error.
    """

    def _sub(match: re.Match[str]) -> str:
        var = match.group(1)
        if var not in os.environ:
            raise ConfigError(f"config references unset environment variable {var!r}")
        return os.environ[var]

    return _ENV_VAR_RE.sub(_sub, value)


def coerce_bool(key: str, value: object, default: bool) -> bool:
    """Coerce a config value to ``bool``, accepting common string spellings."""
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        norm = value.strip().lower()
        if norm in _TRUE_STRINGS:
            return True
        if norm in _FALSE_STRINGS:
            return False
    raise ConfigError(f"{key} must be a boolean, got {value!r}")


def opt_positive_number(key: str, value: object, *, kind: type) -> Any:  # noqa: ANN401 — int or float
    """Coerce an optional config value to a positive ``int``/``float``, or ``None``."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        raise ConfigError(f"{key} must be a number, got {value!r}")
    try:
        coerced = kind(value)
    except ValueError as exc:
        raise ConfigError(f"{key} must be a number, got {value!r}") from exc
    if coerced <= 0:
        raise ConfigError(f"{key} must be positive, got {coerced!r}")
    return coerced


def str_tuple(key: str, value: object) -> tuple[str, ...]:
    """Coerce a config value to a tuple of strings (rejecting a bare string)."""
    if value is None:
        return ()
    if isinstance(value, str) or not isinstance(value, (list, tuple)):
        raise ConfigError(f"{key} must be a list of strings, got {value!r}")
    if not all(isinstance(item, str) for item in value):
        raise ConfigError(f"{key} must contain only strings")
    return tuple(value)


def str_map(key: str, value: object, *, interpolate: bool) -> dict[str, str]:
    """Coerce a config value to a ``dict[str, str]``, optionally env-interpolated."""
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ConfigError(f"{key} must be a mapping")
    out: dict[str, str] = {}
    for k, v in value.items():
        text = str(v)
        out[str(k)] = interpolate_env(text) if interpolate else text
    return out


__all__ = [
    "coerce_bool",
    "interpolate_env",
    "opt_positive_number",
    "str_map",
    "str_tuple",
]

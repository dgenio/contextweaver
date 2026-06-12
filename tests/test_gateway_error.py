"""Tests for contextweaver.adapters.gateway_error (#485).

Covers the structured upstream-error taxonomy, the ``retryable`` hint, detail
redaction, and the exception classifier.
"""

from __future__ import annotations

import asyncio

import pytest

from contextweaver.adapters.gateway_error import (
    GatewayError,
    classify_upstream_exception,
    redact_upstream_detail,
)

# ---------------------------------------------------------------------------
# GatewayError shape — retryable round-trip
# ---------------------------------------------------------------------------


def test_to_dict_includes_retryable() -> None:
    err = GatewayError(code="UPSTREAM_TIMEOUT", message="took too long", retryable=True)
    payload = err.to_dict()
    assert payload == {
        "error": "UPSTREAM_TIMEOUT",
        "message": "took too long",
        "path": "",
        "retryable": True,
    }


def test_from_dict_round_trip() -> None:
    err = GatewayError(
        code="AUTH_FAILED",
        message="bad token",
        path="crm:lookup#abcd1234",
        retryable=False,
        details={"path": ["arg"]},
    )
    restored = GatewayError.from_dict(err.to_dict())
    assert restored == err


def test_from_dict_defaults_retryable_false() -> None:
    restored = GatewayError.from_dict({"error": "UPSTREAM_ERROR", "message": "boom"})
    assert restored.retryable is False


# ---------------------------------------------------------------------------
# redact_upstream_detail
# ---------------------------------------------------------------------------


def test_redaction_strips_control_chars_and_collapses_whitespace() -> None:
    # ESC (\x1b) and NUL (\x00) are control chars; stripping them neutralises
    # any terminal-escape injection. Newlines/tabs collapse to single spaces.
    raw = "line one\nline\ttwo\x1b\x00  three"
    cleaned = redact_upstream_detail(raw)
    assert "\n" not in cleaned
    assert "\t" not in cleaned
    assert "\x1b" not in cleaned
    assert "\x00" not in cleaned
    assert cleaned == "line one line two three"


def test_redaction_caps_length() -> None:
    cleaned = redact_upstream_detail("x" * 1000, max_len=32)
    assert len(cleaned) == 32
    assert cleaned.endswith("…")


def test_redaction_short_string_unchanged() -> None:
    assert redact_upstream_detail("connection refused") == "connection refused"


# ---------------------------------------------------------------------------
# classify_upstream_exception
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("exc", "code", "retryable"),
    [
        (TimeoutError("slow"), "UPSTREAM_TIMEOUT", True),
        (asyncio.TimeoutError(), "UPSTREAM_TIMEOUT", True),
        (ConnectionError("refused"), "UPSTREAM_UNAVAILABLE", True),
        (RuntimeError("401 Unauthorized"), "AUTH_FAILED", False),
        (RuntimeError("403 Forbidden: permission denied"), "PERMISSION_DENIED", False),
        (RuntimeError("429 too many requests"), "RATE_LIMITED", True),
        (RuntimeError("upstream timed out waiting"), "UPSTREAM_TIMEOUT", True),
        (RuntimeError("transport collapsed"), "UPSTREAM_ERROR", False),
    ],
)
def test_classification(exc: BaseException, code: str, retryable: bool) -> None:
    assert classify_upstream_exception(exc) == (code, retryable)

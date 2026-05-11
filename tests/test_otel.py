"""Tests for contextweaver.extras.otel — OpenTelemetry integration.

The whole module is skipped when the ``[otel]`` extra is not installed, with
one exception: a single test that imports the module and asserts the friendly
ImportError surfaces. This guarantees both code paths are covered without
requiring opentelemetry in the default CI install.
"""

from __future__ import annotations

import importlib

import pytest


def _otel_available() -> bool:
    try:
        importlib.import_module("opentelemetry.trace")
        importlib.import_module("opentelemetry.metrics")
    except ImportError:
        return False
    return True


HAS_OTEL = _otel_available()


# ---------------------------------------------------------------------------
# Import-error path (always runs — covers the no-extra case)
# ---------------------------------------------------------------------------


def test_import_error_message_when_extra_missing() -> None:
    """If opentelemetry is missing, importing extras.otel must guide the user."""
    if HAS_OTEL:
        # Cannot meaningfully test the ImportError path when the extra IS installed.
        pytest.skip("opentelemetry is installed; ImportError path not exercised here")
    with pytest.raises(ImportError, match=r"\[otel\]"):
        importlib.import_module("contextweaver.extras.otel")


# ---------------------------------------------------------------------------
# Functional path (runs only when [otel] is installed)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_OTEL, reason="opentelemetry not installed ([otel] extra)")
def test_otel_event_hook_constructs() -> None:
    from contextweaver.extras.otel import OTelEventHook

    hook = OTelEventHook(service_name="cw-test")
    # Tracer and meter handles are created at construction time.
    assert hook._tracer is not None
    assert hook._meter is not None


@pytest.mark.skipif(not HAS_OTEL, reason="opentelemetry not installed ([otel] extra)")
def test_otel_event_hook_satisfies_event_hook_protocol() -> None:
    from contextweaver.extras.otel import OTelEventHook
    from contextweaver.protocols import EventHook

    assert isinstance(OTelEventHook(), EventHook)


@pytest.mark.skipif(not HAS_OTEL, reason="opentelemetry not installed ([otel] extra)")
def test_otel_event_hook_records_build() -> None:
    from contextweaver.envelope import BuildStats, ContextPack
    from contextweaver.extras.otel import OTelEventHook
    from contextweaver.types import Phase

    pack = ContextPack(
        prompt="",
        stats=BuildStats(
            tokens_per_section={"body": 100},
            total_candidates=5,
            included_count=4,
            dropped_count=1,
            header_footer_tokens=20,
        ),
        phase=Phase.answer,
    )
    OTelEventHook().on_context_built(pack)  # must not raise


@pytest.mark.skipif(not HAS_OTEL, reason="opentelemetry not installed ([otel] extra)")
def test_otel_event_hook_route_completed() -> None:
    from contextweaver.extras.otel import OTelEventHook

    OTelEventHook().on_route_completed(["a", "b", "c"])  # must not raise

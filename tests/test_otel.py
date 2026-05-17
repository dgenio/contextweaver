"""Tests for contextweaver.extras.otel — OpenTelemetry GenAI integration (#224).

The whole module is skipped when the ``[otel]`` extra is not installed, with
one exception: a single test that imports the module and asserts the friendly
ImportError surfaces. This guarantees both code paths are covered without
requiring opentelemetry in the default CI install.

The functional tests use the OTel SDK's :class:`InMemorySpanExporter` so the
assertions check actual emitted span names + attribute keys, not the hook's
internal state.  This is what makes the SemConv-name claim verifiable.
"""

from __future__ import annotations

import importlib

import pytest


def _otel_available() -> bool:
    try:
        importlib.import_module("opentelemetry.trace")
        importlib.import_module("opentelemetry.metrics")
        importlib.import_module("opentelemetry.semconv._incubating.attributes.gen_ai_attributes")
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
        pytest.skip("opentelemetry is installed; ImportError path not exercised here")
    with pytest.raises(ImportError, match=r"\[otel\]"):
        importlib.import_module("contextweaver.extras.otel")


# ---------------------------------------------------------------------------
# Test helpers — wire an InMemorySpanExporter for assertions
# ---------------------------------------------------------------------------


if HAS_OTEL:  # pragma: no cover - exercised only with [otel]
    from opentelemetry import trace as _trace
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
        InMemorySpanExporter,
    )

    # OTel's global ``TracerProvider`` can only be set once per process.
    # We install the in-memory exporter at module import time and re-use
    # the same exporter across tests (clearing between cases).
    _EXPORTER: InMemorySpanExporter | None = InMemorySpanExporter()
    _PROVIDER = TracerProvider()
    _PROVIDER.add_span_processor(SimpleSpanProcessor(_EXPORTER))
    _trace.set_tracer_provider(_PROVIDER)
else:  # pragma: no cover - the no-extra path
    _EXPORTER = None


@pytest.fixture
def span_exporter() -> InMemorySpanExporter:
    """Yield the module-level :class:`InMemorySpanExporter`, cleared before use."""
    if not HAS_OTEL or _EXPORTER is None:
        pytest.skip("opentelemetry not installed ([otel] extra)")
    _EXPORTER.clear()
    return _EXPORTER


# ---------------------------------------------------------------------------
# Functional path (runs only when [otel] is installed)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not HAS_OTEL, reason="opentelemetry not installed ([otel] extra)")
def test_otel_event_hook_constructs() -> None:
    from contextweaver.extras.otel import OTelEventHook

    hook = OTelEventHook(service_name="cw-test")
    assert hook._tracer is not None
    assert hook._meter is not None
    assert hook._emit_experimental is False


@pytest.mark.skipif(not HAS_OTEL, reason="opentelemetry not installed ([otel] extra)")
def test_otel_event_hook_satisfies_event_hook_protocol() -> None:
    from contextweaver.extras.otel import OTelEventHook
    from contextweaver.protocols import EventHook

    assert isinstance(OTelEventHook(), EventHook)


@pytest.mark.skipif(not HAS_OTEL, reason="opentelemetry not installed ([otel] extra)")
def test_invoke_agent_span_shape_matches_genai_semconv(span_exporter: InMemorySpanExporter) -> None:
    """``on_context_built`` must emit an ``invoke_agent`` span with stable SemConv attrs."""
    from opentelemetry.semconv._incubating.attributes import gen_ai_attributes as ga

    from contextweaver.envelope import BuildStats, ContextPack
    from contextweaver.extras.otel import (
        GEN_AI_OPERATION_INVOKE_AGENT,
        GEN_AI_SYSTEM_CONTEXTWEAVER,
        OTelEventHook,
    )
    from contextweaver.types import Phase

    pack = ContextPack(
        prompt="x" * 100,
        stats=BuildStats(
            tokens_per_section={"body": 100, "facts": 50},
            total_candidates=5,
            included_count=4,
            dropped_count=1,
            dedup_removed=2,
            header_footer_tokens=20,
        ),
        phase=Phase.answer,
    )
    OTelEventHook(service_name="cw-test").on_context_built(pack)

    spans = span_exporter.get_finished_spans()
    invoke = [s for s in spans if s.name == GEN_AI_OPERATION_INVOKE_AGENT]
    assert len(invoke) == 1, f"expected one invoke_agent span, got {[s.name for s in spans]}"
    attrs = dict(invoke[0].attributes or {})
    # GenAI SemConv attributes:
    assert attrs[ga.GEN_AI_SYSTEM] == GEN_AI_SYSTEM_CONTEXTWEAVER
    assert attrs[ga.GEN_AI_OPERATION_NAME] == GEN_AI_OPERATION_INVOKE_AGENT
    # prompt_tokens = sum(100, 50) + 20 = 170
    assert attrs[ga.GEN_AI_USAGE_INPUT_TOKENS] == 170
    # Engine-specific contextweaver attributes:
    assert attrs["contextweaver.phase"] == "answer"
    assert attrs["contextweaver.candidates.total"] == 5
    assert attrs["contextweaver.candidates.included"] == 4
    assert attrs["contextweaver.candidates.dropped"] == 1
    assert attrs["contextweaver.candidates.dedup_removed"] == 2


@pytest.mark.skipif(not HAS_OTEL, reason="opentelemetry not installed ([otel] extra)")
def test_execute_tool_span_shape_matches_genai_semconv(span_exporter: InMemorySpanExporter) -> None:
    """``on_route_completed`` must emit an ``execute_tool`` span with rank-1 tool name."""
    from opentelemetry.semconv._incubating.attributes import gen_ai_attributes as ga

    from contextweaver.extras.otel import (
        GEN_AI_OPERATION_EXECUTE_TOOL,
        GEN_AI_SYSTEM_CONTEXTWEAVER,
        OTelEventHook,
    )

    OTelEventHook(service_name="cw-test").on_route_completed(
        ["billing.invoices.search", "billing.invoices.list", "billing.invoices.get"]
    )

    spans = span_exporter.get_finished_spans()
    execs = [s for s in spans if s.name == GEN_AI_OPERATION_EXECUTE_TOOL]
    assert len(execs) == 1, f"expected one execute_tool span, got {[s.name for s in spans]}"
    attrs = dict(execs[0].attributes or {})
    assert attrs[ga.GEN_AI_SYSTEM] == GEN_AI_SYSTEM_CONTEXTWEAVER
    assert attrs[ga.GEN_AI_OPERATION_NAME] == GEN_AI_OPERATION_EXECUTE_TOOL
    # Rank-1 tool name surfaces under the canonical SemConv attribute.
    assert attrs[ga.GEN_AI_TOOL_NAME] == "billing.invoices.search"
    assert attrs["contextweaver.routing.candidate_count"] == 3
    # Full candidate list lives under contextweaver.* (not SemConv yet).
    assert tuple(attrs["contextweaver.routing.candidate_ids"]) == (
        "billing.invoices.search",
        "billing.invoices.list",
        "billing.invoices.get",
    )


@pytest.mark.skipif(not HAS_OTEL, reason="opentelemetry not installed ([otel] extra)")
def test_execute_tool_span_handles_empty_candidate_list(
    span_exporter: InMemorySpanExporter,
) -> None:
    """``on_route_completed([])`` should not raise and should omit GEN_AI_TOOL_NAME."""
    from opentelemetry.semconv._incubating.attributes import gen_ai_attributes as ga

    from contextweaver.extras.otel import GEN_AI_OPERATION_EXECUTE_TOOL, OTelEventHook

    OTelEventHook(service_name="cw-test").on_route_completed([])
    spans = span_exporter.get_finished_spans()
    execs = [s for s in spans if s.name == GEN_AI_OPERATION_EXECUTE_TOOL]
    assert len(execs) == 1
    attrs = dict(execs[0].attributes or {})
    assert ga.GEN_AI_TOOL_NAME not in attrs
    assert attrs["contextweaver.routing.candidate_count"] == 0


@pytest.mark.skipif(not HAS_OTEL, reason="opentelemetry not installed ([otel] extra)")
def test_experimental_flag_defaults_off() -> None:
    """By default, raw prompt content + experimental attrs must NOT be emitted."""
    from contextweaver.extras.otel import OTelEventHook

    hook = OTelEventHook(service_name="cw-test")
    assert hook._emit_experimental is False
    enabled = OTelEventHook(service_name="cw-test", otel_emit_experimental=True)
    assert enabled._emit_experimental is True


@pytest.mark.skipif(not HAS_OTEL, reason="opentelemetry not installed ([otel] extra)")
def test_experimental_flag_gates_prompt_in_span(span_exporter: InMemorySpanExporter) -> None:
    """``otel_emit_experimental=True`` must include prompt text; ``False`` must not."""
    from contextweaver.envelope import BuildStats, ContextPack
    from contextweaver.extras.otel import OTelEventHook
    from contextweaver.types import Phase

    prompt_text = "find all overdue invoices for ACME Corp"
    pack = ContextPack(
        prompt=prompt_text,
        stats=BuildStats(total_candidates=1, included_count=1),
        phase=Phase.answer,
    )

    # Default (experimental off) → prompt must NOT appear.
    OTelEventHook(service_name="cw-test").on_context_built(pack)
    spans_off = span_exporter.get_finished_spans()
    for span in spans_off:
        assert "contextweaver.prompt.rendered" not in (span.attributes or {})

    span_exporter.clear()

    # Experimental on → prompt MUST appear.
    OTelEventHook(service_name="cw-test", otel_emit_experimental=True).on_context_built(pack)
    spans_on = span_exporter.get_finished_spans()
    found = False
    for span in spans_on:
        attrs = dict(span.attributes or {})
        if "contextweaver.prompt.rendered" in attrs:
            assert attrs["contextweaver.prompt.rendered"] == prompt_text
            found = True
    assert found, "experimental span must include contextweaver.prompt.rendered"


@pytest.mark.skipif(not HAS_OTEL, reason="opentelemetry not installed ([otel] extra)")
def test_no_pii_in_default_attributes(span_exporter: InMemorySpanExporter) -> None:
    """Default emission must not include raw queries / descriptions."""
    from contextweaver.envelope import BuildStats, ContextPack
    from contextweaver.extras.otel import OTelEventHook
    from contextweaver.types import Phase

    pack = ContextPack(
        prompt="sensitive customer PII here please redact",
        stats=BuildStats(total_candidates=1, included_count=1),
        phase=Phase.answer,
    )
    OTelEventHook(service_name="cw-test").on_context_built(pack)

    spans = span_exporter.get_finished_spans()
    for span in spans:
        attrs = dict(span.attributes or {})
        for value in attrs.values():
            if isinstance(value, str):
                assert "sensitive" not in value
                assert "PII" not in value


@pytest.mark.skipif(not HAS_OTEL, reason="opentelemetry not installed ([otel] extra)")
def test_firewall_event_uses_contextweaver_namespace(span_exporter: InMemorySpanExporter) -> None:
    """Firewall + exclude events stay under contextweaver.* (no SemConv equivalent yet)."""
    from contextweaver.extras.otel import OTelEventHook
    from contextweaver.types import ContextItem, ItemKind

    hook = OTelEventHook(service_name="cw-test")
    hook.on_firewall_triggered(
        ContextItem(id="x", kind=ItemKind.tool_result, text="big"),
        reason="size_threshold",
    )
    spans = span_exporter.get_finished_spans()
    fw = [s for s in spans if s.name == "contextweaver.context.firewall"]
    assert len(fw) == 1
    attrs = dict(fw[0].attributes or {})
    assert attrs["contextweaver.firewall.reason"] == "size_threshold"
    assert attrs["contextweaver.item.kind"] == "tool_result"

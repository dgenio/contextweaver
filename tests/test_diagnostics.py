"""Tests for structured gateway diagnostics (issues #370 / #378)."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from contextweaver.adapters.gateway_catalog_diagnostics import catalog_diagnostic_summary
from contextweaver.adapters.mcp import mcp_tool_to_selectable
from contextweaver.adapters.mcp_gateway import make_gateway_meta_tools
from contextweaver.adapters.mcp_proxy import make_proxy_meta_tools
from contextweaver.adapters.mcp_upstream import StubUpstream
from contextweaver.adapters.proxy_runtime import ProxyRuntime
from contextweaver.diagnostics import (
    DiagnosticEvent,
    InMemoryDiagnosticSink,
    JsonlDiagnosticSink,
    load_diagnostic_events,
    render_diagnostic_report,
    summarize_diagnostics,
)
from contextweaver.envelope import ResultEnvelope
from contextweaver.tokens import count


def _tool_defs() -> list[dict[str, Any]]:
    return [
        {
            "name": "github.create_issue",
            "description": "Create an issue.",
            "inputSchema": {
                "type": "object",
                "properties": {"title": {"type": "string"}, "body": {"type": "string"}},
                "required": ["title"],
            },
        }
    ]


def test_jsonl_sink_round_trips_events(tmp_path: Path) -> None:
    path = tmp_path / "diagnostics.jsonl"
    sink = JsonlDiagnosticSink(path)
    sink.emit(
        DiagnosticEvent(
            event="browse.completed",
            session_id="s1",
            duration_ms=12.5,
            attributes={"card_count": 2},
        )
    )

    events = load_diagnostic_events(path)

    assert len(events) == 1
    assert events[0].event == "browse.completed"
    assert events[0].attributes == {"card_count": 2}
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert raw["version"] == 1


def test_summary_reports_savings_failures_views_and_latency() -> None:
    events = [
        DiagnosticEvent(
            event="execute.completed",
            session_id="s1",
            tool_id="github:create",
            namespace="github",
            duration_ms=10,
            attributes={"raw_tokens": 100, "compact_tokens": 20},
        ),
        DiagnosticEvent(
            event="execute.failed",
            session_id="s1",
            success=False,
            duration_ms=100,
        ),
        DiagnosticEvent(event="view.completed", session_id="s2", duration_ms=20),
    ]

    summary = summarize_diagnostics(events)

    assert summary["event_count"] == 3
    assert summary["session_count"] == 2
    assert summary["failure_count"] == 1
    assert summary["tokens_saved"] == 80
    assert summary["artifact_view_count"] == 1
    assert summary["latency_ms"] == {"count": 3, "p50": 20, "p95": 100, "max": 100}
    assert "Gateway Diagnostics" in render_diagnostic_report(summary)


def test_catalog_summary_matches_exposed_meta_tool_schemas() -> None:
    raw = _tool_defs()[0]
    item = mcp_tool_to_selectable(raw)
    runtime = ProxyRuntime(StubUpstream([raw]))
    runtime.register_tool_defs_sync([raw])

    gateway = catalog_diagnostic_summary([item], {item.id: raw}, mode="gateway")
    gateway_expected = sum(
        count(json.dumps(tool["inputSchema"], sort_keys=True, separators=(",", ":")))
        for tool in make_gateway_meta_tools(runtime)
    )
    assert gateway["exposed_schema_tokens"] == gateway_expected

    proxy = catalog_diagnostic_summary([item], {item.id: raw}, mode="transparent")
    proxy_expected = count('{"type":"object"}') + sum(
        count(json.dumps(tool["inputSchema"], sort_keys=True, separators=(",", ":")))
        for tool in make_proxy_meta_tools(runtime)
    )
    assert proxy["exposed_schema_tokens"] == proxy_expected


async def test_proxy_runtime_emits_sanitized_operation_events() -> None:
    async def handler(name: str, args: dict[str, Any]) -> dict[str, Any]:
        _ = name, args
        return {"content": [{"type": "text", "text": "x" * 800}], "isError": False}

    sink = InMemoryDiagnosticSink()
    runtime = ProxyRuntime(
        StubUpstream(_tool_defs(), handler=handler),
        diagnostic_sink=sink,
        session_id="session-test",
    )
    runtime.register_tool_defs_sync(_tool_defs())
    tool_id = runtime.list_tool_ids()[0]
    runtime.browse(query="secret query")
    runtime.hydrate(tool_id)
    result = await runtime.execute(tool_id, {"title": "private title", "body": "private body"})
    assert isinstance(result, ResultEnvelope)
    handle = result.artifacts[0].handle
    runtime.view(handle, {"type": "head", "chars": 10})

    events = sink.events()
    assert [event.event for event in events] == [
        "catalog.loaded",
        "browse.completed",
        "hydrate.completed",
        "execute.completed",
        "view.completed",
    ]
    execute_event = next(event for event in events if event.event == "execute.completed")
    assert execute_event.attributes["arg_keys"] == ["body", "title"]
    encoded = json.dumps([event.to_dict() for event in events])
    assert "secret query" not in encoded
    assert "private title" not in encoded
    assert "private body" not in encoded
    assert execute_event.attributes["raw_tokens"] > execute_event.attributes["compact_tokens"]
    assert result.firewall_stats is not None
    assert result.firewall_stats.triggered is True


async def test_proxy_runtime_emits_failure_event() -> None:
    sink = InMemoryDiagnosticSink()
    runtime = ProxyRuntime(StubUpstream(_tool_defs()), diagnostic_sink=sink)
    runtime.register_tool_defs_sync(_tool_defs())

    await runtime.execute(runtime.list_tool_ids()[0], {})

    failure = sink.events()[-1]
    assert failure.event == "execute.failed"
    assert failure.success is False
    assert failure.attributes["error_code"] == "ARGS_INVALID"

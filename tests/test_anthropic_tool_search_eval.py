"""Network-free contract tests for the Anthropic Tool Search benchmark adapter."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT / "benchmarks"))
sys.path.insert(0, str(ROOT / "src"))

import e2e_quality as legacy  # noqa: E402
from providers import anthropic_tool_search as anthropic_eval  # noqa: E402


class _FakeResponse:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    def __enter__(self) -> "_FakeResponse":
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")


def _task_and_tool() -> tuple[legacy.Task, legacy.SelectableItem]:
    task = legacy.load_tasks()[0]
    tool = next(item for item in legacy._build_catalog() if item.id == task.expected_tool)
    return task, tool


def test_tool_definitions_use_real_bm25_search_and_defer_capabilities() -> None:
    _task, tool = _task_and_tool()
    tools, reverse = anthropic_eval._tool_definitions([tool])

    assert tools[0] == {
        "type": "tool_search_tool_bm25_20251119",
        "name": "tool_search_tool_bm25",
    }
    assert tools[1]["defer_loading"] is True
    provider_name = str(tools[1]["name"])
    assert "." not in provider_name
    assert reverse[provider_name] == tool.id


def test_provider_callback_preserves_search_blocks_and_sums_usage(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    task, tool = _task_and_tool()
    safe_name = anthropic_eval._safe_tool_name(tool.id)
    calls: list[dict[str, Any]] = []
    responses = iter(
        [
            {
                "content": [
                    {"type": "server_tool_use", "id": "srvtoolu_1", "name": "tool_search_tool_bm25", "input": {"query": "refund payment"}},
                    {
                        "type": "tool_search_tool_result",
                        "tool_use_id": "srvtoolu_1",
                        "content": {
                            "type": "tool_search_tool_search_result",
                            "tool_references": [{"type": "tool_reference", "tool_name": safe_name}],
                        },
                    },
                    {"type": "tool_use", "id": "toolu_1", "name": safe_name, "input": {}},
                ],
                "stop_reason": "tool_use",
                "usage": {"input_tokens": 100, "output_tokens": 10},
            },
            {
                "content": [{"type": "text", "text": "The refund operation completed."}],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 120, "output_tokens": 20},
            },
        ]
    )

    def fake_urlopen(request: Any, timeout: float) -> _FakeResponse:
        assert timeout == 5.0
        calls.append(json.loads(request.data.decode("utf-8")))
        return _FakeResponse(next(responses))

    monkeypatch.setattr(anthropic_eval.urllib.request, "urlopen", fake_urlopen)
    config = anthropic_eval.AnthropicToolSearchConfig(
        api_key="secret-test-key",
        model="claude-test-model",
        timeout_s=5.0,
        input_usd_per_mtok=2.0,
        output_usd_per_mtok=10.0,
    )
    callback = anthropic_eval.make_provider_native_fn(config)
    result = callback(task, [tool], legacy._synthetic_history(turns=2))

    assert result.chosen_tool == tool.id
    assert "refund" in result.answer.lower()
    assert result.prompt_tokens == 220
    assert result.output_tokens == 30
    assert result.cost_usd == pytest.approx(0.00074)
    assert len(calls) == 2

    first_tools = calls[0]["tools"]
    assert first_tools[0]["type"] == "tool_search_tool_bm25_20251119"
    assert first_tools[1]["defer_loading"] is True

    # Anthropic requires the assistant server-tool/search-reference content to
    # be sent back unchanged before the selected tool_result.
    second_messages = calls[1]["messages"]
    assistant_content = second_messages[1]["content"]
    assert assistant_content[0]["type"] == "server_tool_use"
    assert assistant_content[1]["type"] == "tool_search_tool_result"
    assert second_messages[2]["content"][0]["type"] == "tool_result"
    assert second_messages[2]["content"][0]["tool_use_id"] == "toolu_1"


def test_missing_credentials_fail_before_network(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.setenv("CW_E2E_ANTHROPIC_MODEL", "claude-test-model")
    with pytest.raises(ValueError, match="ANTHROPIC_API_KEY"):
        anthropic_eval.AnthropicToolSearchConfig.from_env()


def test_model_must_be_explicit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "not-a-real-key")
    monkeypatch.delenv("CW_E2E_ANTHROPIC_MODEL", raising=False)
    with pytest.raises(ValueError, match="CW_E2E_ANTHROPIC_MODEL"):
        anthropic_eval.AnthropicToolSearchConfig.from_env()


def test_http_error_does_not_echo_api_key(monkeypatch: pytest.MonkeyPatch) -> None:
    def fail_urlopen(_request: Any, timeout: float) -> _FakeResponse:
        assert timeout == 5.0
        raise anthropic_eval.urllib.error.URLError("offline")

    monkeypatch.setattr(anthropic_eval.urllib.request, "urlopen", fail_urlopen)
    config = anthropic_eval.AnthropicToolSearchConfig(
        api_key="super-secret-value",
        model="claude-test-model",
        timeout_s=5.0,
    )
    client = anthropic_eval.AnthropicMessagesClient(config)
    with pytest.raises(RuntimeError) as exc_info:
        client.create(messages=[{"role": "user", "content": "hello"}])
    assert "super-secret-value" not in str(exc_info.value)

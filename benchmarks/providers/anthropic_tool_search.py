"""Opt-in real Anthropic Tool Search baseline for the adversarial eval (#445).

This module uses only the public Messages HTTP API and stdlib networking; it is
never imported by normal package code.  It implements Anthropic's GA Tool
Search shape documented at:

https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool

Key benchmark properties:

- the actual ``tool_search_tool_bm25_20251119`` server tool is used;
- every candidate capability is sent as a full tool definition with
  ``defer_loading: true``;
- the search tool itself is non-deferred, satisfying the API requirement;
- the assistant response, including server-tool search/reference blocks, is
  passed back unchanged before the synthetic benchmark tool result;
- provider-reported token usage is summed across the selection and final-answer
  requests;
- ContextWeaver+native uses the *same* callback with only the offered catalog
  changed by the six-arm harness.

No API key, model id, or price is hard-coded.  Secrets are accepted only from
environment variables by :func:`from_env` and never included in exceptions.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any

from adversarial_eval import ProviderObservation
from e2e_quality import Task
from contextweaver.types import ContextItem, SelectableItem

_API_URL = "https://api.anthropic.com/v1/messages"
_API_VERSION = "2023-06-01"
_TOOL_SEARCH = {
    "type": "tool_search_tool_bm25_20251119",
    "name": "tool_search_tool_bm25",
}
_SAFE_TOOL_RE = re.compile(r"[^a-zA-Z0-9_-]+")


@dataclass(frozen=True)
class AnthropicToolSearchConfig:
    """Explicit run provenance and accounting inputs for one real evaluation."""

    api_key: str
    model: str
    max_tokens: int = 512
    timeout_s: float = 90.0
    input_usd_per_mtok: float = 0.0
    output_usd_per_mtok: float = 0.0

    @classmethod
    def from_env(cls) -> "AnthropicToolSearchConfig":
        api_key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
        model = os.environ.get("CW_E2E_ANTHROPIC_MODEL", "").strip()
        if not api_key:
            raise ValueError("ANTHROPIC_API_KEY is required for the real Tool Search benchmark")
        if not model:
            raise ValueError(
                "CW_E2E_ANTHROPIC_MODEL is required; pin the exact model used for the report"
            )
        return cls(
            api_key=api_key,
            model=model,
            max_tokens=int(os.environ.get("CW_E2E_ANTHROPIC_MAX_TOKENS", "512")),
            timeout_s=float(os.environ.get("CW_E2E_ANTHROPIC_TIMEOUT_S", "90")),
            input_usd_per_mtok=float(
                os.environ.get("CW_E2E_ANTHROPIC_INPUT_USD_PER_MTOK", "0")
            ),
            output_usd_per_mtok=float(
                os.environ.get("CW_E2E_ANTHROPIC_OUTPUT_USD_PER_MTOK", "0")
            ),
        )


class AnthropicMessagesClient:
    """Minimal Messages API client with no SDK/runtime dependency."""

    def __init__(self, config: AnthropicToolSearchConfig) -> None:
        self.config = config

    def create(self, *, messages: list[dict[str, Any]], tools: list[dict[str, Any]] | None = None) -> tuple[dict[str, Any], float]:
        payload: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": self.config.max_tokens,
            "messages": messages,
        }
        if tools is not None:
            payload["tools"] = tools
        request = urllib.request.Request(
            _API_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={
                "content-type": "application/json",
                "x-api-key": self.config.api_key,
                "anthropic-version": _API_VERSION,
            },
            method="POST",
        )
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(request, timeout=self.config.timeout_s) as response:  # noqa: S310 - fixed HTTPS endpoint
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            # Read the provider body for useful classification, but never echo
            # request headers/key.  Truncate to avoid turning benchmark errors
            # into an uncontrolled output surface.
            body = exc.read().decode("utf-8", errors="replace")[:1000]
            raise RuntimeError(f"Anthropic Messages API returned HTTP {exc.code}: {body}") from None
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Anthropic Messages API request failed: {exc.reason}") from None
        latency_ms = (time.perf_counter() - started) * 1000
        parsed = json.loads(raw)
        if not isinstance(parsed, dict):
            raise RuntimeError("Anthropic Messages API returned a non-object response")
        return parsed, latency_ms


def _safe_tool_name(tool_id: str) -> str:
    """Map arbitrary ContextWeaver ids to stable provider-safe tool names."""
    stem = _SAFE_TOOL_RE.sub("_", tool_id).strip("_")[:42] or "tool"
    digest = hashlib.sha256(tool_id.encode("utf-8")).hexdigest()[:10]
    return f"cw_{stem}_{digest}"


def _tool_definitions(catalog: list[SelectableItem]) -> tuple[list[dict[str, Any]], dict[str, str]]:
    definitions: list[dict[str, Any]] = [dict(_TOOL_SEARCH)]
    provider_to_cw: dict[str, str] = {}
    for item in catalog:
        name = _safe_tool_name(item.id)
        provider_to_cw[name] = item.id
        schema = item.args_schema if isinstance(item.args_schema, dict) else None
        if not schema:
            schema = {"type": "object", "properties": {}}
        definitions.append(
            {
                "name": name,
                "description": f"ContextWeaver capability {item.id}. {item.description}",
                "input_schema": schema,
                "defer_loading": True,
            }
        )
    return definitions, provider_to_cw


def _history_text(history: list[ContextItem]) -> str:
    if not history:
        return ""
    return "\n".join(f"- {item.text}" for item in history)


def _text_content(payload: dict[str, Any]) -> str:
    content = payload.get("content", [])
    if not isinstance(content, list):
        return ""
    return "\n".join(
        str(block.get("text", ""))
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ).strip()


def _first_tool_use(payload: dict[str, Any]) -> dict[str, Any] | None:
    content = payload.get("content", [])
    if not isinstance(content, list):
        return None
    for block in content:
        if isinstance(block, dict) and block.get("type") == "tool_use":
            return block
    return None


def _usage(payload: dict[str, Any]) -> tuple[int, int]:
    usage = payload.get("usage", {})
    if not isinstance(usage, dict):
        return 0, 0
    return int(usage.get("input_tokens", 0) or 0), int(usage.get("output_tokens", 0) or 0)


def _cost(config: AnthropicToolSearchConfig, input_tokens: int, output_tokens: int) -> float:
    return (
        input_tokens / 1_000_000 * config.input_usd_per_mtok
        + output_tokens / 1_000_000 * config.output_usd_per_mtok
    )


def make_provider_native_fn(config: AnthropicToolSearchConfig):
    """Return the real provider-native callback expected by adversarial_eval.run."""
    client = AnthropicMessagesClient(config)

    def provider_native(
        task: Task,
        offered_catalog: list[SelectableItem],
        history: list[ContextItem],
    ) -> ProviderObservation:
        tools, provider_to_cw = _tool_definitions(offered_catalog)
        history_text = _history_text(history)
        user_text = (
            "You are in a benchmark. Use the available tools when needed. "
            "After receiving a tool result, answer in one short sentence and mention the tool/result domain.\n\n"
            f"Conversation context:\n{history_text}\n\nCurrent request: {task.query}"
        )
        messages: list[dict[str, Any]] = [{"role": "user", "content": user_text}]
        first, first_latency = client.create(messages=messages, tools=tools)
        first_in, first_out = _usage(first)
        tool_use = _first_tool_use(first)
        if tool_use is None:
            return ProviderObservation(
                chosen_tool=None,
                answer=_text_content(first),
                prompt_tokens=first_in,
                output_tokens=first_out,
                latency_ms=first_latency,
                cost_usd=_cost(config, first_in, first_out),
            )

        provider_name = str(tool_use.get("name", ""))
        chosen_tool = provider_to_cw.get(provider_name)
        tool_use_id = str(tool_use.get("id", ""))
        assistant_content = first.get("content", [])
        if not isinstance(assistant_content, list) or not tool_use_id:
            raise RuntimeError("Anthropic tool-use response is missing content or tool_use id")

        # The benchmark tools are non-destructive fixtures. We execute the
        # selected synthetic capability by returning its stable identity rather
        # than performing any real side effect. The model must then ground the
        # final answer in that result.
        tool_result = json.dumps(
            {
                "ok": chosen_tool is not None,
                "capability_id": chosen_tool or provider_name,
                "result": f"Synthetic benchmark execution completed for {chosen_tool or provider_name}",
            },
            sort_keys=True,
        )
        second_messages = [
            messages[0],
            {"role": "assistant", "content": assistant_content},
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": tool_use_id,
                        "content": tool_result,
                    }
                ],
            },
        ]
        second, second_latency = client.create(messages=second_messages, tools=tools)
        second_in, second_out = _usage(second)
        input_tokens = first_in + second_in
        output_tokens = first_out + second_out
        return ProviderObservation(
            chosen_tool=chosen_tool,
            answer=_text_content(second),
            prompt_tokens=input_tokens,
            output_tokens=output_tokens,
            latency_ms=first_latency + second_latency,
            cost_usd=_cost(config, input_tokens, output_tokens),
        )

    return provider_native


def make_prompt_call_fn(config: AnthropicToolSearchConfig):
    """Return a plain real-model call for the four non-native prompt arms."""
    client = AnthropicMessagesClient(config)

    def call(prompt: str) -> str:
        response, _latency = client.create(messages=[{"role": "user", "content": prompt}])
        return _text_content(response)

    return call

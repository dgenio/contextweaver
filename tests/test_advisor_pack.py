"""Tests for the planning-only AdvisorPack escalation (issue #741)."""

from __future__ import annotations

import json

import pytest

from contextweaver.context.advisor_pack import (
    AdvisorRequest,
    AdvisorResponse,
    ask_advisor,
    build_advisor_prompt,
)
from contextweaver.exceptions import ConfigError, PolicyViolationError
from contextweaver.extras.llm_guard import GuardedCallFn, GuardPolicy
from contextweaver.tokens import count


def _request(**overrides: object) -> AdvisorRequest:
    base: dict = {
        "question": "Which migration order is safest?",
        "options": ["schema-first", "data-first"],
        "context_summary": "Two services share the users table.",
        "constraints": ["no downtime"],
    }
    base.update(overrides)
    return AdvisorRequest(**base)  # type: ignore[arg-type]


def test_prompt_is_deterministic_and_complete() -> None:
    request = _request()
    first = build_advisor_prompt(request)
    second = build_advisor_prompt(request)
    assert first == second
    assert "Which migration order is safest?" in first
    assert "1. schema-first" in first and "2. data-first" in first
    assert "no downtime" in first
    assert "strict JSON" in first
    assert "advice only" in first.lower() or "planning advice only" in first


def test_prompt_truncates_context_to_budget() -> None:
    long_context = "word " * 5000
    request = _request(context_summary=long_context, budget_tokens=50)
    prompt = build_advisor_prompt(request)
    context_part = prompt.split("Context:\n", 1)[1].split("\n\nAnswer", 1)[0]
    assert count(context_part) <= 50


def test_valid_json_parsed() -> None:
    def call_fn(prompt: str) -> str:
        return json.dumps(
            {
                "advice": "Schema first avoids double writes.",
                "preferred_option": "schema-first",
                "confidence": "high",
            }
        )

    response = ask_advisor(_request(), call_fn, provider_metadata={"model": "test-1"})
    assert response.preferred_option == "schema-first"
    assert response.confidence == "high"
    assert response.advice == "Schema first avoids double writes."
    assert response.provider_metadata == {"model": "test-1"}


def test_off_list_option_is_nulled_with_marker() -> None:
    def call_fn(prompt: str) -> str:
        return json.dumps({"advice": "Do X.", "preferred_option": "big-bang-rewrite"})

    response = ask_advisor(_request(), call_fn)
    assert response.preferred_option is None
    assert "[advisor: option not in candidate set]" in response.advice
    assert "Do X." in response.advice


def test_malformed_output_degrades_to_raw_advice() -> None:
    def call_fn(prompt: str) -> str:
        return "I think schema-first, but here is prose not JSON."

    response = ask_advisor(_request(), call_fn)
    assert response.preferred_option is None
    assert response.confidence is None
    assert "schema-first" in response.advice
    assert response.raw == "I think schema-first, but here is prose not JSON."


def test_invalid_confidence_is_dropped() -> None:
    def call_fn(prompt: str) -> str:
        return json.dumps({"advice": "ok", "confidence": "certain"})

    assert ask_advisor(_request(), call_fn).confidence is None


def test_guard_cap_propagates_to_caller() -> None:
    calls = {"n": 0}

    def call_fn(prompt: str) -> str:
        calls["n"] += 1
        return json.dumps({"advice": "ok"})

    # One-shot: guard_policy wraps the call for this single escalation.
    assert ask_advisor(_request(), call_fn, guard_policy=GuardPolicy(max_calls=1)).advice == "ok"

    # Persistent budget across escalations: a GuardedCallFn IS a call_fn, so
    # callers reuse one instance and cap rejections propagate unchanged.
    guarded = GuardedCallFn(call_fn, GuardPolicy(max_calls=1))
    assert ask_advisor(_request(), guarded).advice == "ok"
    with pytest.raises(PolicyViolationError):
        ask_advisor(_request(), guarded)
    assert calls["n"] == 2


def test_request_validation() -> None:
    with pytest.raises(ConfigError):
        AdvisorRequest(question="   ")
    with pytest.raises(ConfigError):
        AdvisorRequest(question="q", budget_tokens=0)


def test_serde_round_trips() -> None:
    request = _request()
    assert AdvisorRequest.from_dict(request.to_dict()) == request
    response = AdvisorResponse(
        advice="a", preferred_option="schema-first", confidence="low", raw="{}"
    )
    assert AdvisorResponse.from_dict(response.to_dict()) == response
    with pytest.raises(ConfigError):
        AdvisorResponse.from_dict({"advice": "a", "confidence": "certain"})

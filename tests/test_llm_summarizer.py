"""Tests for contextweaver.extras.llm_summarizer (issue #26, Phase 2).

The LLM-backed plugins take a user-supplied ``call_fn`` and require no
third-party SDK, so every test here runs in the default install — the
``call_fn`` is a plain Python stub.  Coverage spans the happy path, the
fact-line parser, the rule-based fallback on every failure mode, and the
firewall wiring (Phase 1) actually delegating to an injected plugin.
"""

from __future__ import annotations

from contextweaver.context.firewall import apply_firewall
from contextweaver.extras.llm_summarizer import (
    LlmExtractor,
    LlmSummarizer,
    _parse_fact_lines,
)
from contextweaver.protocols import Extractor, Summarizer
from contextweaver.store.artifacts import InMemoryArtifactStore
from contextweaver.types import ContextItem, ItemKind


def _raise(_: str) -> str:
    raise RuntimeError("model unavailable")


# ---------------------------------------------------------------------------
# _parse_fact_lines
# ---------------------------------------------------------------------------


def test_parse_fact_lines_strips_bullets_numbers_and_blanks() -> None:
    text = "- alpha\n\n2. beta\n* gamma\n   \n3) delta"
    assert _parse_fact_lines(text) == ["alpha", "beta", "gamma", "delta"]


def test_parse_fact_lines_dedupes_preserving_order() -> None:
    assert _parse_fact_lines("alpha\n- alpha\nbeta") == ["alpha", "beta"]


def test_parse_fact_lines_empty_returns_empty() -> None:
    assert _parse_fact_lines("\n   \n") == []


# ---------------------------------------------------------------------------
# LlmSummarizer
# ---------------------------------------------------------------------------


def test_summarizer_is_protocol_instance() -> None:
    assert isinstance(LlmSummarizer(lambda p: "ok"), Summarizer)


def test_summarizer_returns_stripped_model_output() -> None:
    captured: dict[str, str] = {}

    def call(prompt: str) -> str:
        captured["prompt"] = prompt
        return "  42 results matched.\n"

    summ = LlmSummarizer(call, system_prompt="SUM:\n")
    assert summ.summarize("huge raw output", {"tool": "search"}) == "42 results matched."
    assert captured["prompt"] == "SUM:\nhuge raw output"


def test_summarizer_truncates_input_to_max_input() -> None:
    seen: dict[str, str] = {}

    def call(prompt: str) -> str:
        seen["prompt"] = prompt
        return "summary"

    LlmSummarizer(call, max_input=5, system_prompt="").summarize("x" * 100)
    assert seen["prompt"] == "xxxxx"


def test_summarizer_falls_back_on_exception() -> None:
    # The default RuleBasedSummarizer turns an empty input into "(empty)".
    assert LlmSummarizer(_raise).summarize("") == "(empty)"


def test_summarizer_falls_back_on_blank_completion() -> None:
    out = LlmSummarizer(lambda p: "   ").summarize("error: disk full")
    # Falls back to the rule-based key-line path, which surfaces the error line.
    assert "error: disk full" in out


def test_summarizer_uses_custom_fallback() -> None:
    class StaticFallback:
        def summarize(self, raw: str, metadata: dict[str, object]) -> str:
            return "FALLBACK"

    summ = LlmSummarizer(_raise, fallback=StaticFallback())
    assert summ.summarize("anything") == "FALLBACK"


# ---------------------------------------------------------------------------
# LlmExtractor
# ---------------------------------------------------------------------------


def test_extractor_is_protocol_instance() -> None:
    assert isinstance(LlmExtractor(lambda p: "fact"), Extractor)


def test_extractor_parses_model_list() -> None:
    ext = LlmExtractor(lambda p: "- status: ok\n- count: 3")
    assert ext.extract("raw") == ["status: ok", "count: 3"]


def test_extractor_falls_back_on_exception() -> None:
    facts = LlmExtractor(_raise).extract('{"a": 1, "b": 2}')
    # The default StructuredExtractor reports the JSON shape.
    assert facts and facts[0].startswith("type: object")


def test_extractor_falls_back_on_empty_facts() -> None:
    facts = LlmExtractor(lambda p: "\n  \n").extract('{"k": "v"}')
    assert facts and facts[0].startswith("type: object")


# ---------------------------------------------------------------------------
# Firewall wiring (Phase 1 delegation, exercised end-to-end)
# ---------------------------------------------------------------------------


def _tool_result(text: str) -> ContextItem:
    return ContextItem(id="t1", kind=ItemKind.tool_result, text=text)


def test_firewall_uses_injected_summarizer_and_extractor() -> None:
    store = InMemoryArtifactStore()
    summ = LlmSummarizer(lambda p: "LLM SUMMARY")
    ext = LlmExtractor(lambda p: "- one\n- two")
    processed, envelope = apply_firewall(
        _tool_result("x" * 5000), store, summarizer=summ, extractor=ext
    )
    assert envelope is not None
    assert envelope.status == "ok"
    # 5000 chars > max_input=4000: the summary carries the issue #384
    # omission marker because the model saw a truncated input.
    assert envelope.summary == "LLM SUMMARY [llm summary of first 4000 chars]"
    assert envelope.facts == ["one", "two"]
    assert processed.text == envelope.summary


def test_firewall_with_failing_plugins_still_succeeds_via_fallback() -> None:
    store = InMemoryArtifactStore()
    processed, envelope = apply_firewall(
        _tool_result("error: boom\nstatus: failed"),
        store,
        summarizer=LlmSummarizer(_raise),
        extractor=LlmExtractor(_raise),
    )
    assert envelope is not None
    # Plugins degrade internally to the rule-based path, so the firewall sees
    # a clean "ok" status rather than a degraded one.
    assert envelope.status == "ok"
    assert "error: boom" in envelope.summary
    assert processed.artifact_ref is not None

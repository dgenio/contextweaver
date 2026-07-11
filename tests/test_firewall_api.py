"""Tests for the single-call firewall facade (issues #399, #402, #403, #404, #405, #406)."""

from __future__ import annotations

import json

import pytest

from contextweaver import compact_tool_result, firewalled_tool_result
from contextweaver.context.firewall_api import CW_SIDECAR_KEY, CompactResult
from contextweaver.exceptions import ConfigError, DeterminismError
from contextweaver.store.artifacts import InMemoryArtifactStore
from contextweaver.tokens import count as count_tokens

_BIG = {"invoices": [{"invoiceNumber": f"A-{i}", "amount": i, "status": "due"} for i in range(200)]}


def test_compact_rejects_reserved_cw_key_in_input() -> None:
    """A payload already carrying the reserved _cw key raises (#467)."""
    with pytest.raises(ConfigError, match=CW_SIDECAR_KEY):
        compact_tool_result({"data": 1, CW_SIDECAR_KEY: {"mine": True}})


def test_compact_overwrite_sidecar_escape_hatch() -> None:
    """overwrite_sidecar=True replaces an existing _cw instead of raising (#467)."""
    out = compact_tool_result({"data": 1, CW_SIDECAR_KEY: {"mine": True}}, overwrite_sidecar=True)
    # Small payload → passthrough; the sidecar is replaced with firewall metadata.
    assert out.payload[CW_SIDECAR_KEY]["strategy"] == "passthrough"
    assert "mine" not in out.payload[CW_SIDECAR_KEY]


def test_firewalled_alias_also_guards_reserved_key() -> None:
    """The firewalled_tool_result alias shares the collision guard (#467)."""
    with pytest.raises(ConfigError, match=CW_SIDECAR_KEY):
        firewalled_tool_result({"x": 1, CW_SIDECAR_KEY: {}})


class _FakeLlmSummarizer:
    """Stand-in LLM summariser flagged via the ``is_llm`` provenance marker."""

    is_llm = True

    def summarize(self, raw: str, metadata: dict) -> str:
        return "LLM SUMMARY"


# ---------------------------------------------------------------------------
# #403 — schema-preserving pass-through
# ---------------------------------------------------------------------------


def test_passthrough_preserves_dict_shape_with_sidecar() -> None:
    data = {"response": {"x": 1, "y": 2}}
    out = compact_tool_result(data, threshold_chars=2000)
    assert out.firewalled is False
    # Caller fields are byte-identical; only a namespaced sidecar is added.
    assert out.payload["response"] == {"x": 1, "y": 2}
    assert out.payload[CW_SIDECAR_KEY]["firewalled"] is False
    assert out.payload[CW_SIDECAR_KEY]["strategy"] == "passthrough"
    # Original input dict is not mutated.
    assert CW_SIDECAR_KEY not in data


def test_passthrough_does_not_mutate_input() -> None:
    data = {"a": 1}
    compact_tool_result(data, threshold_chars=2000)
    assert data == {"a": 1}


def test_passthrough_list_returned_unchanged() -> None:
    data = [1, 2, 3]
    out = compact_tool_result(data, threshold_chars=2000)
    assert out.firewalled is False
    assert out.payload == [1, 2, 3]
    assert out.payload is data


def test_explicit_passthrough_strategy_never_offloads_even_when_large() -> None:
    out = compact_tool_result(_BIG, strategy="passthrough")
    assert out.firewalled is False
    assert out.stats.triggered is False
    assert out.payload["invoices"] == _BIG["invoices"]


# ---------------------------------------------------------------------------
# #399 — single-call firewall + #402 stats
# ---------------------------------------------------------------------------


def test_text_strategy_offloads_and_reports_stats() -> None:
    store = InMemoryArtifactStore()
    out = compact_tool_result(_BIG, threshold_chars=100, strategy="text", artifact_store=store)
    assert out.firewalled is True
    assert out.summary is not None
    assert "_cw_summary" in out.payload
    assert out.payload["_cw_artifact_ref"] == out.artifact_ref
    # #402: stats answer "triggered?" and "how much saved?"
    assert out.stats.triggered is True
    assert out.stats.strategy == "summary"
    assert out.stats.original_chars > out.stats.summary_chars
    assert out.stats.chars_saved > 0
    assert out.stats.tokens_saved >= 0
    # Raw payload retained out-of-band.
    assert store.exists(out.artifact_ref)


def test_compact_result_to_dict_round_trips_stats() -> None:
    out = compact_tool_result(_BIG, threshold_chars=100, strategy="text")
    d = out.to_dict()
    assert d["firewalled"] is True
    assert d["stats"]["strategy"] == "summary"
    assert isinstance(out, CompactResult)


def test_firewalled_tool_result_is_alias() -> None:
    assert firewalled_tool_result is compact_tool_result


# ---------------------------------------------------------------------------
# #406 — structured (lossless) firewall via the facade
# ---------------------------------------------------------------------------


def test_structured_strategy_projects_and_keeps_raw_retrievable() -> None:
    store = InMemoryArtifactStore()
    out = compact_tool_result(
        _BIG,
        threshold_chars=100,
        strategy="structured",
        keep=["invoices[].invoiceNumber", "invoices[].amount"],
        artifact_store=store,
    )
    assert out.firewalled is True
    assert out.stats.strategy == "structured"
    assert out.stats.summarized_by_llm is False
    # Allow-listed fields are inline; `status` is dropped from the prompt view.
    first = out.payload["invoices"][0]
    assert set(first) == {"invoiceNumber", "amount"}
    # Dropped fields remain retrievable via drilldown on the stored raw payload.
    raw = json.loads(store.get(out.artifact_ref).decode("utf-8"))
    assert raw["invoices"][0]["status"] == "due"


def test_structured_strategy_requires_keep() -> None:
    with pytest.raises(ConfigError):
        compact_tool_result(_BIG, strategy="structured")


def test_structured_strategy_on_non_json_raises_instead_of_downgrading() -> None:
    # An explicit structured request on free-form text must fail loud rather
    # than silently degrade to a text summary (projection needs JSON).
    free_text = "lorem ipsum " * 500
    with pytest.raises(ConfigError):
        compact_tool_result(free_text, strategy="structured", keep=["a.b"])


# ---------------------------------------------------------------------------
# Review hardening (PR #420): unique handles, JSON-serialisability, empty keep
# ---------------------------------------------------------------------------


def test_same_length_payloads_get_distinct_handles_in_shared_store() -> None:
    # Two different payloads of the *same* serialised length must not collide
    # on the artifact handle when a store is reused across calls.
    store = InMemoryArtifactStore()
    a = {"k": "A" * 5000}
    b = {"k": "B" * 5000}
    out_a = compact_tool_result(a, threshold_chars=100, strategy="text", artifact_store=store)
    out_b = compact_tool_result(b, threshold_chars=100, strategy="text", artifact_store=store)
    assert out_a.artifact_ref != out_b.artifact_ref
    assert "A" * 5000 in store.get(out_a.artifact_ref).decode("utf-8")
    assert "B" * 5000 in store.get(out_b.artifact_ref).decode("utf-8")


def test_handle_is_deterministic_for_identical_payloads() -> None:
    payload = {"k": "Z" * 5000}
    h1 = compact_tool_result(payload, threshold_chars=100, strategy="text").artifact_ref
    h2 = compact_tool_result(payload, threshold_chars=100, strategy="text").artifact_ref
    assert h1 == h2


def test_non_json_serialisable_input_raises_config_error() -> None:
    with pytest.raises(ConfigError):
        compact_tool_result({"bad": {1, 2, 3}}, threshold_chars=0)


def test_empty_keep_does_not_select_structured_or_swallow_error() -> None:
    # An empty allow-list means "no structured projection" — the call must fall
    # through to a clean summary (status ok, facts extracted), not a swallowed
    # ConfigError that downgrades to a partial summary.
    out = compact_tool_result(
        {"status": "ok", "count": 5}, threshold_chars=0, strategy="auto", keep=[]
    )
    assert out.firewalled is True
    assert out.stats.strategy == "summary"


# ---------------------------------------------------------------------------
# #404 — determinism guarantee
# ---------------------------------------------------------------------------


def test_deterministic_default_raises_on_llm_summarizer() -> None:
    with pytest.raises(DeterminismError):
        compact_tool_result(
            _BIG, threshold_chars=100, strategy="text", summarizer=_FakeLlmSummarizer()
        )


def test_non_deterministic_allows_llm_summarizer() -> None:
    out = compact_tool_result(
        _BIG,
        threshold_chars=100,
        strategy="text",
        summarizer=_FakeLlmSummarizer(),
        deterministic=False,
    )
    assert out.stats.summarized_by_llm is True
    assert out.stats.strategy == "llm_summary"
    assert out.payload["_cw_summary"] == "LLM SUMMARY"


def test_structured_is_model_free_under_deterministic() -> None:
    # An LLM summariser is supplied, but the structured path never calls it,
    # so deterministic=True must NOT raise.
    out = compact_tool_result(
        _BIG,
        threshold_chars=100,
        strategy="structured",
        keep=["invoices[].amount"],
        summarizer=_FakeLlmSummarizer(),
        deterministic=True,
    )
    assert out.stats.strategy == "structured"
    assert out.stats.summarized_by_llm is False


# --- secret scrubbing (issue #745) ------------------------------------------

# Assembled from fragments (secret-scanner push protection), as in test_secrets.
_TOKEN = "sk-ant-" + "api03-" + "zY9xW8vU" * 4
_MASK = "[REDACTED-SECRET]"


def test_redact_scrubs_passthrough_dict_leaves() -> None:
    # Sub-threshold dict is passed through shape-unchanged, but string leaves
    # are scrubbed when redact_secrets=True (#745).
    out = compact_tool_result({"note": f"key {_TOKEN}", "n": 7}, redact_secrets=True)
    assert out.firewalled is False
    assert _TOKEN not in out.payload["note"]
    assert _MASK in out.payload["note"]
    assert out.payload["n"] == 7  # non-string scalar untouched, shape preserved


def test_redact_off_by_default_leaves_passthrough_intact() -> None:
    out = compact_tool_result({"note": f"key {_TOKEN}"})
    assert out.payload["note"] == f"key {_TOKEN}"


def test_redact_scrubs_passthrough_string() -> None:
    out = compact_tool_result(f"here is {_TOKEN} done", redact_secrets=True)
    assert out.firewalled is False
    assert _TOKEN not in out.payload
    assert _MASK in out.payload


def test_redact_scrubs_text_summary_branch() -> None:
    # Force the summarizing branch with a large secret-bearing text payload.
    big = f"{_TOKEN} " + "context words here " * 200
    out = compact_tool_result(big, threshold_chars=100, redact_secrets=True)
    assert out.firewalled is True
    assert out.summary is not None
    assert _TOKEN not in out.summary
    assert _TOKEN not in json.dumps(out.payload)


def test_redact_scrubs_structured_projection() -> None:
    big = {"rows": [{"secret": _TOKEN, "id": i} for i in range(80)]}
    out = compact_tool_result(
        big, threshold_chars=100, keep=["rows[].secret", "rows[].id"], redact_secrets=True
    )
    assert out.firewalled is True
    assert _TOKEN not in json.dumps(out.payload)
    assert _MASK in json.dumps(out.payload)


def test_redact_passthrough_stats_measure_actual_returned_payload() -> None:
    # Regression test (PR #771 review): stats.summary_chars/summary_tokens
    # must reflect the payload actually returned (post-scrub), not the
    # pre-redaction input — otherwise the #405 token-counter invariant (and
    # the sidecar's tokens_saved) would be wrong under redaction.
    data = {"note": f"key {_TOKEN}"}
    out = compact_tool_result(data, redact_secrets=True)
    assert out.firewalled is False
    scrubbed_body = {"note": f"key {_MASK}"}
    expected_text = json.dumps(scrubbed_body, sort_keys=True)
    assert out.stats.summary_chars == len(expected_text)
    assert out.stats.summary_tokens == count_tokens(expected_text)
    # The mask text differs in length from the scrubbed secret, so the
    # redacted payload size must diverge from the original input size.
    assert out.stats.summary_chars != out.stats.original_chars


def test_redact_passthrough_stats_match_unredacted_when_off() -> None:
    data = {"note": f"key {_TOKEN}"}
    out = compact_tool_result(data, redact_secrets=False)
    assert out.stats.summary_chars == out.stats.original_chars
    assert out.stats.summary_tokens == out.stats.original_tokens


def test_redact_structured_stats_measure_post_scrub_summary() -> None:
    # Regression test (PR #771 audit): the structured branch must also recompute
    # stats.summary_chars/summary_tokens on the post-scrub summary, mirroring the
    # passthrough/summary branches. apply_firewall measures stats on the
    # pre-scrub summary, so without the recompute the #405 token-counter
    # invariant (and the sidecar's tokens_saved) would be wrong under redaction
    # for structured output.
    big = {"rows": [{"secret": _TOKEN, "id": i} for i in range(80)]}
    out = compact_tool_result(
        big, threshold_chars=100, keep=["rows[].secret", "rows[].id"], redact_secrets=True
    )
    assert out.firewalled is True
    assert out.stats.strategy == "structured"
    assert out.summary is not None
    assert _TOKEN not in out.summary
    assert out.stats.summary_chars == len(out.summary)
    assert out.stats.summary_tokens == count_tokens(out.summary)


# ---------------------------------------------------------------------------
# Issue #384 — auditable LLM summarization on the firewall path
# ---------------------------------------------------------------------------


def test_llm_summary_records_provider_audit_metadata() -> None:
    from contextweaver.extras.llm_summarizer import LlmSummarizer

    store = InMemoryArtifactStore()
    summarizer = LlmSummarizer(
        lambda p: "compact llm view",
        provider_metadata={"provider": "anthropic", "model": "claude-x", "version": "1"},
    )
    result = compact_tool_result(
        "x" * 5000,
        threshold_chars=100,
        artifact_store=store,
        summarizer=summarizer,
        deterministic=False,
    )
    assert result.firewalled is True
    assert result.stats.summarized_by_llm is True
    assert result.stats.strategy == "llm_summary"
    assert result.stats.llm_provider == {
        "provider": "anthropic",
        "model": "claude-x",
        "version": "1",
    }
    # Round-trips through serde (audit survives persistence).
    restored = type(result.stats).from_dict(result.stats.to_dict())
    assert restored.llm_provider == result.stats.llm_provider


def test_llm_summary_links_raw_artifact_and_stores_it_first() -> None:
    from contextweaver.extras.llm_summarizer import LlmSummarizer

    store = InMemoryArtifactStore()
    raw = "y" * 5000

    def call_fn(prompt: str) -> str:
        # The raw artifact must exist BEFORE the model runs (issue #384:
        # the LLM never becomes the only copy of the data).
        assert store.list_refs(), "raw artifact not stored before summarization"
        return "llm view"

    result = compact_tool_result(
        raw,
        threshold_chars=100,
        artifact_store=store,
        summarizer=LlmSummarizer(call_fn),
        deterministic=False,
    )
    assert result.artifact_ref is not None
    stored = store.get(result.artifact_ref).decode("utf-8")
    assert stored == raw


def test_llm_failure_falls_back_deterministically_with_no_audit() -> None:
    from contextweaver.extras.llm_summarizer import LlmSummarizer

    def broken(prompt: str) -> str:
        raise RuntimeError("model down")

    result = compact_tool_result(
        "z" * 5000,
        threshold_chars=100,
        artifact_store=InMemoryArtifactStore(),
        summarizer=LlmSummarizer(broken, provider_metadata={"model": "m"}),
        deterministic=False,
    )
    # Fallback produced a usable summary and the raw artifact survived.
    assert result.firewalled is True
    assert result.summary
    assert result.artifact_ref is not None


def test_deterministic_summary_has_no_llm_provider() -> None:
    result = compact_tool_result(
        "w" * 5000, threshold_chars=100, artifact_store=InMemoryArtifactStore()
    )
    assert result.stats.summarized_by_llm is False
    assert result.stats.llm_provider is None

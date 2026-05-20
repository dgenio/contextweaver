"""Unit tests for benchmark metric helper functions.

These are pure-function tests for the correctness foundation of the benchmark
harness.  Import via sys.path since benchmarks/ is outside src/.
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "benchmarks"))

from benchmark import (
    _MIXED_NAMESPACE_HEAD,
    _SUPPORTED_BACKENDS,
    _capture_environment,
    _estimate_usd_cost,
    _gold_corpus_for_parity,
    _make_catalog,
    _make_mixed_namespace_catalog,
    _matrix_cell_skip_reason,
    _parse_args,
    _precision_at_k,
    _recall_at_k,
    _reciprocal_rank,
    _run_e2e_real_model,
    _run_tiktoken_parity,
)

# Synthetic variant IDs always end with .vN (e.g. billing.charge_customer.v2).
# Natural IDs never match this pattern (billing.invoices.void contains .v but not .vN at end).
_SYNTHETIC_PAT = re.compile(r"[.]v[0-9]+\Z")


def test_precision_at_k() -> None:
    assert _precision_at_k(["a", "b", "c"], ["b"], k=3) == pytest.approx(1 / 3)


def test_precision_at_k_zero_k() -> None:
    assert _precision_at_k(["a"], ["a"], k=0) == 0.0


def test_recall_at_k_full() -> None:
    assert _recall_at_k(["a", "b"], ["a", "b"], k=2) == 1.0


def test_recall_at_k_partial() -> None:
    assert _recall_at_k(["a", "b", "c"], ["a", "d"], k=3) == pytest.approx(0.5)


def test_recall_at_k_empty_expected() -> None:
    assert _recall_at_k(["a"], [], k=1) == 1.0


def test_reciprocal_rank_first_hit() -> None:
    assert _reciprocal_rank(["a", "b"], ["a"]) == 1.0


def test_reciprocal_rank_second_hit() -> None:
    assert _reciprocal_rank(["a", "b"], ["b"]) == pytest.approx(0.5)


def test_reciprocal_rank_no_hit() -> None:
    assert _reciprocal_rank(["a", "b"], ["c"]) == 0.0


def test_make_catalog_natural_pool_no_synthetic() -> None:
    """83-item catalog must be the full natural pool with no synthetic variants."""
    items = _make_catalog(83)
    assert all(not _SYNTHETIC_PAT.search(item.id) for item in items)
    assert len(items) == 83


def test_make_catalog_size_50() -> None:
    items = _make_catalog(50)
    assert len(items) == 50
    assert all(not _SYNTHETIC_PAT.search(item.id) for item in items)


def test_make_catalog_size_1000_has_synthetic() -> None:
    items = _make_catalog(1000)
    assert len(items) == 1000
    assert any(_SYNTHETIC_PAT.search(item.id) for item in items)


def test_parse_args_rejects_unknown_backend() -> None:
    """Typos in --backends should exit code 2 (argparse convention), not crash later."""
    with pytest.raises(SystemExit) as exc_info:
        _parse_args(["--matrix", "--backends", "tfidf,tifdf"])
    assert exc_info.value.code == 2


def test_parse_args_accepts_all_supported_backends() -> None:
    """All three documented backends pass validation."""
    args = _parse_args(["--matrix", "--backends", "tfidf,bm25,fuzzy"])
    assert args.backends == "tfidf,bm25,fuzzy"


def test_parse_args_accepts_subset_of_backends() -> None:
    """A subset of supported backends is also valid."""
    args = _parse_args(["--matrix", "--backends", "tfidf"])
    assert args.backends == "tfidf"


# ---------------------------------------------------------------------------
# Embedding backend wiring (issue #266)
# ---------------------------------------------------------------------------


def test_supported_backends_includes_embedding_choices() -> None:
    assert "embedding_hashing" in _SUPPORTED_BACKENDS
    assert "embedding_st" in _SUPPORTED_BACKENDS


def test_parse_args_accepts_embedding_hashing_backend() -> None:
    args = _parse_args(["--matrix", "--backends", "tfidf,embedding_hashing"])
    assert "embedding_hashing" in args.backends


def test_matrix_cell_skip_reason_is_none_for_default_backends() -> None:
    assert _matrix_cell_skip_reason("tfidf") is None
    assert _matrix_cell_skip_reason("bm25") is None


def test_matrix_cell_skip_reason_embedding_st_when_extra_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``embedding_st`` should emit a skipped row when sentence-transformers is missing."""
    import benchmark as bench_mod

    monkeypatch.setattr(bench_mod, "_SENTENCE_TRANSFORMERS_AVAILABLE", False)
    reason = _matrix_cell_skip_reason("embedding_st")
    assert reason is not None
    assert "sentence-transformers" in reason


# ---------------------------------------------------------------------------
# Mixed-namespace catalog (issue #277)
# ---------------------------------------------------------------------------


def test_mixed_namespace_catalog_size_500() -> None:
    items = _make_mixed_namespace_catalog(500)
    assert len(items) == 500


def test_mixed_namespace_catalog_head_dominates() -> None:
    """The head namespace must contribute the planned share of items (#277)."""
    items = _make_mixed_namespace_catalog(500)
    head_label, head_n = _MIXED_NAMESPACE_HEAD
    head_items = [i for i in items if i.namespace == head_label]
    # The catalog includes the natural 83-item pool plus the planned synthetic
    # head; intersection: exactly ``head_n`` items in the head namespace.
    assert len(head_items) == head_n


def test_mixed_namespace_catalog_long_tail_present() -> None:
    """Some long-tail single-item namespaces must appear (#277)."""
    items = _make_mixed_namespace_catalog(500)
    tail_namespaces = {i.namespace for i in items if i.namespace.startswith("longtail_")}
    # The planner adds 100 long-tail namespaces × 1 item each.  Subject to the
    # 500-cap, at least a few must survive; pin a conservative lower bound so
    # the test does not over-couple to the exact head/mid/small accounting.
    assert len(tail_namespaces) >= 10


def test_mixed_namespace_catalog_keeps_natural_gold_ids() -> None:
    """Natural pool IDs (gold-dataset-mapped) must survive the mixed shape."""
    items = _make_mixed_namespace_catalog(500)
    natural_ids = {i.id for i in items if not i.id.startswith(("longtail_", "analytics_xl"))}
    assert "billing.create_invoice" in natural_ids or any("billing" in i.id for i in items)


# ---------------------------------------------------------------------------
# Environment capture (issue #267)
# ---------------------------------------------------------------------------


def test_capture_environment_returns_populated_fields() -> None:
    env = _capture_environment()
    assert env.system != ""
    assert env.python_version != ""
    assert env.cpu_logical_cores >= 0


# ---------------------------------------------------------------------------
# Tiktoken parity (issue #268)
# ---------------------------------------------------------------------------


def test_gold_corpus_for_parity_extracts_query_strings() -> None:
    gold: list[dict[str, object]] = [
        {"query": "send a notification", "expected": ["x"]},
        {"query": "search for records", "expected": ["y"]},
        {"query": "", "expected": ["z"]},  # blank query must be skipped
        {"expected": ["w"]},  # missing query key must be skipped
    ]
    corpus = _gold_corpus_for_parity(gold)
    assert corpus == ["send a notification", "search for records"]


def test_tiktoken_parity_skips_on_empty_corpus() -> None:
    stats = _run_tiktoken_parity([])
    assert stats.samples == 0
    assert stats.status.startswith("skipped")


def test_tiktoken_parity_runs_or_skips_gracefully() -> None:
    """Status must be either ``ok`` or a clearly-labelled ``skipped: ...`` row."""
    gold = [{"query": "hello world", "expected": ["x"]}]
    stats = _run_tiktoken_parity(gold)
    assert stats.status == "ok" or stats.status.startswith("skipped")
    if stats.status == "ok":
        assert stats.samples == 1
        assert stats.mean_ratio > 0.0


# ---------------------------------------------------------------------------
# End-to-end real-model gate (issue #269)
# ---------------------------------------------------------------------------


def test_e2e_real_model_disabled_by_default() -> None:
    stats = _run_e2e_real_model([], enabled=False)
    assert stats.status == "skipped: offline by default"
    assert stats.samples == 0


def test_e2e_real_model_enabled_but_no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """When the flag is on but env vars are missing, must skip cleanly."""
    monkeypatch.delenv("CW_BENCH_LLM_PROVIDER", raising=False)
    monkeypatch.delenv("CW_BENCH_LLM_API_KEY", raising=False)
    stats = _run_e2e_real_model([], enabled=True)
    assert stats.status.startswith("skipped: missing CW_BENCH_LLM_")


def test_estimate_usd_cost_known_model() -> None:
    """Known provider/model pair returns a positive cost."""
    cost = _estimate_usd_cost("openai", "gpt-4o-mini", 1_000_000, 1_000_000)
    # Rate table: ($0.15 prompt + $0.60 completion) per 1M tokens.
    assert cost == pytest.approx(0.75)


def test_estimate_usd_cost_unknown_model_returns_zero() -> None:
    assert _estimate_usd_cost("acme", "model-x", 1000, 1000) == 0.0

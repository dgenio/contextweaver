"""Token-estimation calibration benchmark (issue #493).

Quantifies how far the dependency-free heuristic and the tiktoken default
diverge from real provider tokenizers across a fixed, multi-shape corpus, so
budget enforcement and published token-savings numbers can be trusted across
the whole provider matrix the adapters support.

Three counting paths:

1. ``heuristic`` — :class:`~contextweaver.protocols.HeuristicEstimator`
   (offline, deterministic, script-aware). Always available.
2. ``tiktoken`` — the ``cl100k_base`` encoding via
   :func:`contextweaver.tokens.count`. Requires the BPE file (a warmed
   ``TIKTOKEN_CACHE_DIR`` in air-gapped CI); reported as ``null`` when the
   encoding cannot be loaded.
3. provider ``count_tokens`` APIs (Anthropic / Gemini) — **opt-in, credentialed,
   never run in CI**. Enabled only when ``CW_TOKEN_CALIBRATION_PROVIDERS`` lists
   providers whose counter has been registered via
   :func:`contextweaver.tokens.register_estimator`.

The benchmark writes a machine-readable JSON snapshot and renders the committed
divergence table at ``docs/token_calibration.md``. It is **not** a CI gate (the
tiktoken/provider columns are environment-dependent); regenerate it in an
environment with a warmed tiktoken cache to refresh the tokenizer columns.

Usage::

    python benchmarks/token_calibration.py            # write JSON + Markdown
    python benchmarks/token_calibration.py --stdout    # print the table only
"""

from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone
from pathlib import Path

from contextweaver import tokens
from contextweaver.protocols import HeuristicEstimator, TiktokenEstimator

# ---------------------------------------------------------------------------
# Fixed corpus — ≥4 shapes, multilingual (acceptance criterion #1).
# ---------------------------------------------------------------------------

_CORPUS: dict[str, list[str]] = {
    "prose_en": [
        "The quarterly report shows revenue growth across every region.",
        "Please summarise the attached document and list any open action items.",
    ],
    "prose_cjk": [
        "これはツールの実行結果です。請求書はまだ支払われていません。",
        "这是一个工具调用的结果，发票尚未支付，需要尽快发送提醒。",
        "이것은 도구 실행 결과입니다. 미지급 송장에 대한 알림이 필요합니다.",
    ],
    "json": [
        '{"id": 42, "status": "ok", "rows": [{"a": 1, "b": 2}, {"a": 3, "b": 4}]}',
        '{"user": {"name": "Ada", "roles": ["admin", "billing"]}, "active": true}',
    ],
    "code": [
        "def add(a: int, b: int) -> int:\n    return a + b  # trivial helper\n",
        "for i in range(10):\n    if i % 2 == 0:\n        print(i)\n",
    ],
    "logs": [
        "2026-06-11T10:32:22Z INFO gateway: tool_execute id=billing:invoices ok in 42ms",
        "2026-06-11T10:32:23Z WARN proxy: upstream slow (1200ms) namespace=crm",
    ],
}

#: Recorded reference model for the tiktoken column (acceptance: record a model).
_TIKTOKEN_MODEL = "cl100k_base"

_DOC_PATH = Path(__file__).resolve().parents[1] / "docs" / "token_calibration.md"
_JSON_PATH = Path(__file__).resolve().parent / "results" / "token_calibration.json"


def _provider_counters() -> dict[str, object]:
    """Return registered provider counters requested via the opt-in env var.

    ``CW_TOKEN_CALIBRATION_PROVIDERS`` is a comma-separated list of names that
    must already be registered through
    :func:`contextweaver.tokens.register_estimator` (the harness never imports a
    provider SDK itself — that stays out of core and CI).
    """
    raw = os.environ.get("CW_TOKEN_CALIBRATION_PROVIDERS", "").strip()
    if not raw:
        return {}
    registered = tokens.registered_estimators()
    out: dict[str, object] = {}
    for name in (n.strip() for n in raw.split(",") if n.strip()):
        if name in registered:
            out[name] = registered[name]
    return out


def _rel_error(estimate: int, reference: int) -> float | None:
    """Signed relative error of *estimate* versus *reference* (``None`` if 0)."""
    if reference <= 0:
        return None
    return (estimate - reference) / reference


def compute() -> dict[str, object]:
    """Compute the calibration snapshot (pure, deterministic given the corpus)."""
    heuristic = HeuristicEstimator()
    # Instantiate tiktoken directly (do NOT go through get_token_counter): the
    # registry resolves names first, so a counter registered under the encoding
    # name would otherwise replace and corrupt the tiktoken reference column.
    tiktoken_counter = TiktokenEstimator(_TIKTOKEN_MODEL)
    tiktoken_ok = tiktoken_counter.name.startswith("tiktoken/")
    providers = _provider_counters()

    shapes: list[dict[str, object]] = []
    for shape, samples in _CORPUS.items():
        chars = sum(len(s) for s in samples)
        h_tokens = sum(heuristic.estimate(s) for s in samples)
        tt_tokens = sum(tiktoken_counter.estimate(s) for s in samples) if tiktoken_ok else None
        row: dict[str, object] = {
            "shape": shape,
            "samples": len(samples),
            "chars": chars,
            "heuristic_tokens": h_tokens,
            "tiktoken_tokens": tt_tokens,
            "heuristic_vs_tiktoken_rel_error": (
                _rel_error(h_tokens, tt_tokens) if tt_tokens is not None else None
            ),
        }
        for pname, counter in providers.items():
            p_tokens = sum(int(counter.estimate(s)) for s in samples)  # type: ignore[attr-defined]
            row[f"{pname}_tokens"] = p_tokens
            row[f"heuristic_vs_{pname}_rel_error"] = _rel_error(h_tokens, p_tokens)
            if tt_tokens is not None:
                row[f"tiktoken_vs_{pname}_rel_error"] = _rel_error(tt_tokens, p_tokens)
        shapes.append(row)

    return {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%d"),
        "tiktoken_model": _TIKTOKEN_MODEL,
        "tiktoken_available": tiktoken_ok,
        "providers": sorted(providers),
        "shapes": shapes,
    }


def _fmt_pct(value: float | None) -> str:
    """Render a relative error as a signed percentage, or ``n/a``."""
    if value is None:
        return "n/a"
    return f"{value:+.0%}"


def render_markdown(snapshot: dict[str, object]) -> str:
    """Render *snapshot* as the committed divergence table."""
    shapes: list[dict[str, object]] = snapshot["shapes"]  # type: ignore[assignment]
    providers: list[str] = snapshot["providers"]  # type: ignore[assignment]
    tiktoken_ok = bool(snapshot["tiktoken_available"])

    lines: list[str] = []
    lines.append("# Token-estimation calibration")
    lines.append("")
    lines.append(
        "Divergence of the dependency-free heuristic "
        "(`HeuristicEstimator`, `heuristic/v2`) and the `tiktoken` default from "
        "real tokenizers, across fixed corpus shapes. Generated by "
        "`benchmarks/token_calibration.py` (issue #493)."
    )
    lines.append("")
    lines.append(f"- Generated: {snapshot['generated_at']}")
    lines.append(f"- tiktoken model: `{snapshot['tiktoken_model']}`")
    if not tiktoken_ok:
        lines.append(
            "- ⚠️ **tiktoken encoding unavailable when generated** (offline / no "
            "warmed `TIKTOKEN_CACHE_DIR`). The `tiktoken` and relative-error "
            "columns read `n/a`; regenerate in an environment with a warmed "
            "cache to populate them."
        )
    if providers:
        lines.append(f"- Provider tokenizers: {', '.join(providers)}")
    else:
        lines.append(
            "- Provider tokenizers (Anthropic / Gemini): not run "
            "(opt-in via `CW_TOKEN_CALIBRATION_PROVIDERS` + a registered counter; "
            "never run in CI)."
        )
    lines.append("")

    header = ["Shape", "Chars", "Heuristic", "tiktoken", "Heuristic vs tiktoken"]
    for pname in providers:
        header.append(f"{pname}")
        header.append(f"Heuristic vs {pname}")
    lines.append("| " + " | ".join(header) + " |")
    lines.append("|" + "|".join(["---"] * len(header)) + "|")

    for row in shapes:
        tt = row["tiktoken_tokens"]
        cells = [
            f"`{row['shape']}`",
            str(row["chars"]),
            str(row["heuristic_tokens"]),
            str(tt) if tt is not None else "n/a",
            _fmt_pct(row["heuristic_vs_tiktoken_rel_error"]),  # type: ignore[arg-type]
        ]
        for pname in providers:
            cells.append(str(row.get(f"{pname}_tokens", "n/a")))
            cells.append(_fmt_pct(row.get(f"heuristic_vs_{pname}_rel_error")))  # type: ignore[arg-type]
        lines.append("| " + " | ".join(cells) + " |")

    lines.append("")
    lines.append(
        "Reading the table: a large negative *Heuristic vs tiktoken* error on "
        "`prose_cjk` is the failure mode issue #525 fixes — the script-aware "
        "heuristic keeps CJK within roughly ±30% of tiktoken instead of "
        "under-counting ~4×. For Latin prose, JSON, code, and logs the heuristic "
        "tracks `len // 4`, which is adequate for budget headroom decisions."
    )
    lines.append("")
    return "\n".join(lines)


def main() -> int:
    """CLI entry point: write the JSON snapshot and the Markdown table."""
    parser = argparse.ArgumentParser(description="Token-estimation calibration benchmark")
    parser.add_argument(
        "--stdout", action="store_true", help="print the Markdown table instead of writing files"
    )
    args = parser.parse_args()

    snapshot = compute()
    markdown = render_markdown(snapshot)

    if args.stdout:
        print(markdown)  # noqa: T201 — CLI/benchmark script, not library code
        return 0

    _JSON_PATH.parent.mkdir(parents=True, exist_ok=True)
    _JSON_PATH.write_text(json.dumps(snapshot, indent=2, ensure_ascii=False) + "\n", "utf-8")
    _DOC_PATH.write_text(markdown, "utf-8")
    print(f"wrote {_JSON_PATH} and {_DOC_PATH}")  # noqa: T201 — benchmark script
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

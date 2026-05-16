#!/usr/bin/env python3
"""Naïve-concat baseline harness (issue #215).

Computes the token cost and a coverage proxy for a "dump everything" baseline
against which contextweaver's context-pipeline output can be honestly
compared. The harness intentionally avoids LLM calls (repo policy:
``benchmarks/README.md`` §"no LLM calls, no external network access") and
relies on structural signals only.

Two roles:

1. **Library function** ``compute_naive_delta`` — invoked from
   ``benchmarks/benchmark.py`` per scenario row to emit the additive
   ``naive_delta`` block in ``benchmarks/results/latest.json``.

2. **Standalone script** — re-reads an existing ``latest.json``, augments each
   ``context`` row with a ``naive_delta`` block, and rewrites the file. Useful
   when the matrix run finishes and the user wants the naïve numbers without
   re-running the full benchmark.

Coverage formula (deterministic, documented):

    coverage_pct = round(items_included / max(event_count, 1) * 100, 2)

This is the "fraction of conversation events that survived the budget-aware
pipeline" — a structural proxy for answer-fidelity that does **not** require
an LLM judge. The formula is bounded ``[0, 100]`` and stable across runs
because it derives directly from the deterministic pipeline counts already in
each context row.

Naïve token count:

    naive_tokens = len(cl100k_base.encode(catalog_schemas + scenario_text))

where ``catalog_schemas`` is the canonical ``examples/sample_catalog.json``
rendered as one ``{id}: {description}`` line per tool, and ``scenario_text``
is the concatenation of every ``text`` field in the scenario JSONL in source
order.

Token reduction percentage:

    pct_reduction = round((1 - cw_tokens / naive_tokens) * 100, 2)
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

_ROOT = Path(__file__).resolve().parent.parent
_CATALOG_PATH = _ROOT / "examples" / "sample_catalog.json"


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------


def _count_tokens(text: str) -> int:
    """Count ``cl100k_base`` tokens, falling back to ``len // 4`` when missing.

    ``tiktoken`` is a core contextweaver dependency
    (``pyproject.toml:dependencies``) so the import normally succeeds; the
    fallback exists so the script can run in stripped-down environments
    (e.g. a fresh sdist without the full dependency closure installed).
    """
    try:
        import tiktoken  # noqa: PLC0415 — optional / lazy import path

        enc = tiktoken.get_encoding("cl100k_base")
        return len(enc.encode(text))
    except Exception:
        # Same heuristic as ``CharDivFourEstimator`` in the library.
        return max(1, len(text) // 4)


def _render_catalog_schema(catalog_path: Path) -> str:
    """Render the canonical sample catalog as a naïve "all tool schemas" blob.

    Matches the shape of what an agent would receive without contextweaver:
    every tool's id and description joined into a single string, one per line.
    """
    items: list[dict[str, Any]] = json.loads(catalog_path.read_text(encoding="utf-8"))
    parts = []
    for it in sorted(items, key=lambda x: str(x.get("id", ""))):
        idf = str(it.get("id", ""))
        desc = str(it.get("description", "") or it.get("name", ""))
        tags = ",".join(str(t) for t in it.get("tags", []) or [])
        parts.append(f"{idf}: {desc} [tags: {tags}]")
    return "\n".join(parts)


def _scenario_text(scenario_path: Path) -> str:
    """Concatenate every ``text`` field in a scenario JSONL, in source order."""
    parts: list[str] = []
    for line in scenario_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        text = ev.get("text")
        if isinstance(text, str):
            parts.append(text)
    return "\n".join(parts)


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


def compute_naive_delta(scenario_path: Path, context_row: dict[str, Any]) -> dict[str, Any]:
    """Compute the ``naive_delta`` block for a single context-row scenario.

    Args:
        scenario_path: Filesystem path to the ``.jsonl`` scenario file.
        context_row: A row from ``latest.json.context`` — must carry the
            ``prompt_tokens``, ``items_included``, and ``event_count`` fields
            already emitted by ``benchmarks/benchmark.py``.

    Returns:
        A dict with ``naive_tokens``, ``cw_tokens``, ``pct_reduction``, and
        ``coverage_pct`` keys. All numeric values are rounded for
        byte-identical reruns across executions on the same seed.
    """
    catalog_blob = _render_catalog_schema(_CATALOG_PATH)
    scenario_blob = _scenario_text(scenario_path)
    naive_tokens = _count_tokens(catalog_blob + "\n" + scenario_blob)
    cw_tokens = int(context_row.get("prompt_tokens", 0))
    pct_reduction = round((1.0 - cw_tokens / naive_tokens) * 100, 2) if naive_tokens > 0 else 0.0
    event_count = max(int(context_row.get("event_count", 0)), 1)
    items_included = int(context_row.get("items_included", 0))
    coverage_pct = round(items_included / event_count * 100, 2)
    return {
        "naive_tokens": int(naive_tokens),
        "cw_tokens": int(cw_tokens),
        "pct_reduction": float(pct_reduction),
        "coverage_pct": float(coverage_pct),
    }


def annotate_latest_json(latest_path: Path, scenarios_dir: Path) -> int:
    """Augment ``latest.json`` in place with a ``naive_delta`` per context row.

    Returns the number of rows annotated. Skips rows whose scenario JSONL
    cannot be located (e.g. a renamed file) and prints a warning to stderr.
    """
    payload = json.loads(latest_path.read_text(encoding="utf-8"))
    rows = payload.get("context")
    if not isinstance(rows, list):
        print(f"latest.json: no context list at {latest_path}", file=sys.stderr)
        return 0
    annotated = 0
    for row in rows:
        if not isinstance(row, dict):
            continue
        name = str(row.get("scenario", ""))
        path = scenarios_dir / f"{name}.jsonl"
        if not path.is_file():
            print(f"skip: no scenario JSONL for {name!r}", file=sys.stderr)
            continue
        row["naive_delta"] = compute_naive_delta(path, row)
        annotated += 1
    latest_path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    return annotated


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--latest",
        default=str(_ROOT / "benchmarks" / "results" / "latest.json"),
        help="Path to latest.json to annotate in place.",
    )
    parser.add_argument(
        "--scenarios-dir",
        default=str(_ROOT / "benchmarks" / "scenarios"),
        help="Directory containing the scenario JSONL files.",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    latest = Path(args.latest)
    scen = Path(args.scenarios_dir)
    if not latest.is_file():
        print(f"latest.json not found: {latest}", file=sys.stderr)
        return 1
    if not scen.is_dir():
        print(f"scenarios dir not found: {scen}", file=sys.stderr)
        return 1
    n = annotate_latest_json(latest, scen)
    print(f"Annotated {n} context row(s) with naive_delta in {latest}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

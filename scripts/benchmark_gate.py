#!/usr/bin/env python3
"""Gate benchmark quality regressions with tolerance bands (issue #491).

Companion to ``scripts/benchmark_delta.py`` (the informational sticky PR
comment). Where the delta script *describes* head-vs-base movement, this
script *enforces* it: a PR that regresses a gated quality metric beyond its
band — recall@k, MRR, precision@k, token-savings, compaction ratio — exits
non-zero so CI can
block the merge. Latency cells are never gated (runner variance).

The gate compares a head ``latest.json`` against the committed base
``latest.json`` cell-by-cell, keyed by identity (catalog size, backend×size,
or scenario), so a regression in any one cell is surfaced with its location.

The script is **stdlib-only on the hot path**; the YAML config is parsed with a
lazy ``yaml`` import (a core dependency) only when ``--gating-config`` is read,
so the import is paid in CI after ``pip install -e .`` runs.

Usage::

    python scripts/benchmark_gate.py --base base.json --head head.json
    python scripts/benchmark_gate.py --base base.json --head head.json \\
        --gating-config benchmarks/gating.yaml
    python scripts/benchmark_gate.py --base base.json --head head.json --override

Exit codes: ``0`` when every gated cell is within band (or ``--override`` is
set), ``1`` when any gated cell regresses beyond its band, ``2`` on bad input.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from _benchmark_gate_cells import GateViolation, band_key
from _benchmark_gate_cells import evaluate_gate as _evaluate_gate

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_GATING_CONFIG = REPO_ROOT / "benchmarks" / "gating.yaml"

# Mirrors benchmarks/gating.yaml so the gate has a safe default when no config
# file is present (e.g. a partial checkout). Kept in sync with that file.
DEFAULT_BANDS: dict[str, float] = {
    "recall_at_k": 1.0,
    "mrr": 1.0,
    "precision_at_k": 1.0,
    "token_savings_pct": 2.0,
    "avg_compaction_ratio": 5.0,
}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class GatingConfig:
    """Resolved gate configuration: per-metric bands plus the override label."""

    bands: dict[str, float]
    override_label: str = "benchmark-accepted"


def load_gating_config(path: Path | None) -> GatingConfig:
    """Load bands from *path*; fall back to :data:`DEFAULT_BANDS` when absent.

    The defaults are a safety net for a *missing* or unparseable config (e.g. a
    partial checkout), not a floor. When *path* exists, parses to a mapping, and
    carries a ``quality`` block, that block is authoritative: only metrics whose
    band is a non-negative number are gated (a ``0`` band means "no regression
    tolerated"), and a config that sets every metric to ``gating: false`` (so no
    band resolves) deliberately gates nothing rather than silently reverting to
    the defaults. A present config that omits ``quality`` entirely is treated as
    incomplete and keeps the defaults.
    """
    if path is None or not path.exists():
        return GatingConfig(bands=dict(DEFAULT_BANDS))
    import yaml  # lazy: keeps the import off the no-config path

    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, dict):
        return GatingConfig(bands=dict(DEFAULT_BANDS))
    override = str(raw.get("override_label", "benchmark-accepted"))
    if "quality" not in raw:
        return GatingConfig(bands=dict(DEFAULT_BANDS), override_label=override)
    bands: dict[str, float] = {}
    for metric, spec in (raw.get("quality") or {}).items():
        if not isinstance(spec, dict):
            continue
        band = spec.get(band_key(str(metric)))
        if isinstance(band, (int, float)) and band >= 0:
            bands[str(metric)] = float(band)
    return GatingConfig(bands=bands, override_label=override)


def evaluate_gate(
    base: dict[str, object], head: dict[str, object], config: GatingConfig
) -> list[GateViolation]:
    """Return gated regressions and required cells missing from *head*."""
    return _evaluate_gate(base, head, config.bands)


def render_report(
    violations: list[GateViolation],
    *,
    overridden: bool,
    override_label: str = "benchmark-accepted",
) -> str:
    """Render a deterministic plain-text gate report.

    *override_label* names the configured downgrade label (from the gating
    config) so the report points at the exact label CI checks for.
    """
    if not violations:
        return "benchmark gate: PASS — all gated quality metrics within band."
    lines = [f"benchmark gate: {len(violations)} metric violation(s):"]
    lines.extend(f"  - {v.describe()}" for v in violations)
    lines.append("")
    if overridden:
        lines.append(
            f"Override label '{override_label}' present — downgrading failure to a warning."
        )
    else:
        lines.append(
            f"Fix the regression, or apply the '{override_label}' label with a rationale in "
            "the PR description to accept it."
        )
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=__doc__.splitlines()[0] if __doc__ else None,
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--base", required=True, help="Baseline (committed) latest.json")
    parser.add_argument("--head", required=True, help="PR head latest.json")
    parser.add_argument(
        "--gating-config",
        default=str(DEFAULT_GATING_CONFIG),
        help="Path to gating.yaml (bands + override label)",
    )
    parser.add_argument(
        "--override",
        action="store_true",
        help="Downgrade any gate failure to a warning (exit 0).",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        base = json.loads(Path(args.base).read_text(encoding="utf-8"))
        head = json.loads(Path(args.head).read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    except json.JSONDecodeError as exc:
        print(f"error: malformed JSON: {exc}", file=sys.stderr)
        return 2

    config = load_gating_config(Path(args.gating_config) if args.gating_config else None)
    violations = evaluate_gate(base, head, config)
    print(render_report(violations, overridden=args.override, override_label=config.override_label))
    if violations and not args.override:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""Private helpers for :mod:`contextweaver.context.firewall_api`.

Extracted so the single-call facade module stays within its size ceiling while
the ``redact_secrets`` option (issue #745) is threaded through it.  Not public
API — imported only by ``firewall_api.py``.
"""

from __future__ import annotations

import json
from typing import Any

from contextweaver.envelope import FirewallStats
from contextweaver.exceptions import ConfigError
from contextweaver.tokens import count as count_tokens


def to_text(data: Any) -> tuple[str, str]:  # noqa: ANN401 — arbitrary JSON tool result
    """Return ``(text, media_type)`` for *data* (deterministic JSON for non-str).

    Raises:
        ConfigError: If *data* is not JSON-serialisable.  We deliberately do
            *not* fall back to ``json.dumps(default=str)`` — silently
            stringifying arbitrary objects would break the determinism promise
            (e.g. ``str(set)`` ordering) and could leak ``repr`` details such as
            memory addresses into the prompt.
    """
    if isinstance(data, str):
        return data, "text/plain"
    try:
        return json.dumps(data, sort_keys=True), "application/json"
    except TypeError as exc:
        raise ConfigError(
            "compact_tool_result requires JSON-serialisable data (dict / list / "
            f"str); got a non-serialisable value: {exc}"
        ) from exc


def sidecar(stats: FirewallStats) -> dict[str, Any]:
    """Build the reserved ``_cw`` sidecar payload from *stats*."""
    return {
        "firewalled": stats.triggered,
        "strategy": stats.strategy,
        "artifact_ref": stats.artifact_ref,
        "original_chars": stats.original_chars,
        "summary_chars": stats.summary_chars,
        "summarized_by_llm": stats.summarized_by_llm,
    }


def truncate_to_budget(summary: str, budget: int, token_model: str | None) -> str:
    """Truncate *summary* so its token count stays within *budget* (soft cap)."""
    if budget <= 0 or count_tokens(summary, model=token_model) <= budget:
        return summary
    # Approximate: ~4 chars/token, then trim until under budget.
    trimmed = summary[: max(budget * 4, 1)]
    while trimmed and count_tokens(trimmed, model=token_model) > budget:
        trimmed = trimmed[: max(len(trimmed) - 64, 0)]
    return trimmed + "…"

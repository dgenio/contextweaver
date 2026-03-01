"""A2A (Agent-to-Agent) adapter for contextweaver.

Provides helpers for converting A2A agent descriptors and results,
and loading A2A session JSONL files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from contextweaver.protocols import Summarizer
from contextweaver.store.artifacts import InMemoryArtifactStore
from contextweaver.summarize.rules import RuleBasedSummarizer
from contextweaver.types import (
    ContextItem,
    ItemKind,
    ResultEnvelope,
    SelectableItem,
)


def agent_response_to_envelope(
    response: dict[str, Any],
    artifact_store: InMemoryArtifactStore | None = None,
    summarizer: Summarizer | None = None,
) -> ResultEnvelope:
    """Agent descriptor response -> ResultEnvelope."""
    status_raw = response.get("status", "ok")
    status: Literal["ok", "partial", "error"] = (
        status_raw if status_raw in ("ok", "partial", "error") else "ok"
    )  # type: ignore[assignment]
    text = response.get("text", response.get("content", str(response)))
    summ = summarizer or RuleBasedSummarizer()
    summary = summ.summarize(str(text))

    return ResultEnvelope(
        status=status,
        summary=summary,
        facts={"source": "a2a"},
        provenance={"source": "a2a"},
    )


def agent_to_item(agent_info: dict[str, Any]) -> SelectableItem:
    """Agent descriptor -> SelectableItem (kind="agent")."""
    name = agent_info.get("name", "unknown")
    desc = agent_info.get("description", "")
    skills = agent_info.get("skills", [])
    tags = [s.get("name", s) if isinstance(s, dict) else str(s) for s in skills]

    return SelectableItem(
        id=f"a2a.{name}",
        kind="agent",
        name=name,
        description=desc,
        tags=tags,
        namespace="a2a",
        metadata={"source": "a2a", "agent_info": agent_info},
    )


def load_a2a_session_jsonl(path: str | Path) -> list[ContextItem]:
    """Load JSONL of A2A messages -> ContextItems."""
    items: list[ContextItem] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            item = _a2a_line_to_item(data)
            if item:
                items.append(item)
    return items


def _a2a_line_to_item(data: dict[str, Any]) -> ContextItem | None:
    """Convert a single JSONL line to a ContextItem."""
    event_type = data.get("type", "")
    item_id = data.get("id", "")
    timestamp = data.get("timestamp", 0.0)

    kind_map = {
        "user_turn": ItemKind.USER_TURN,
        "tool_call": ItemKind.TOOL_CALL,
        "tool_result": ItemKind.TOOL_RESULT,
        "agent_msg": ItemKind.AGENT_MSG,
    }
    kind = kind_map.get(event_type)
    if kind is None:
        return None

    text = data.get("text", data.get("content", str(data)))
    parent_id = data.get("tool_call_id")

    return ContextItem(
        id=item_id,
        kind=kind,
        text=text,
        token_estimate=len(text) // 4,
        metadata={
            "timestamp": timestamp,
            "source": data.get("source", "a2a"),
        },
        parent_id=parent_id,
    )

"""MCP (Model Context Protocol) adapter for contextweaver.

Provides helpers for converting MCP tool definitions and results,
and loading MCP session JSONL files.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Literal

from contextweaver.protocols import Extractor, Summarizer
from contextweaver.store.artifacts import InMemoryArtifactStore
from contextweaver.summarize.extract import StructuredExtractor
from contextweaver.summarize.rules import RuleBasedSummarizer
from contextweaver.types import (
    ArtifactRef,
    ContextItem,
    ItemKind,
    ResultEnvelope,
    SelectableItem,
)


def mcp_tool_to_item(mcp_schema: dict[str, Any]) -> SelectableItem:
    """MCP tool schema -> SelectableItem (kind="tool"). Pure Python."""
    name = mcp_schema.get("name", "unknown")
    desc = mcp_schema.get("description", "")
    input_schema = mcp_schema.get("inputSchema")
    annotations = mcp_schema.get("annotations", {})

    return SelectableItem(
        id=f"mcp.{name}",
        kind="tool",
        name=name,
        description=desc,
        tags=list(annotations.get("tags", [])),
        namespace="mcp",
        args_schema=input_schema,
        side_effects=annotations.get("sideEffects", False),
        cost_hint=annotations.get("costHint", "low"),
        metadata={"source": "mcp", "annotations": annotations},
    )


def mcp_result_to_envelope(
    mcp_result: dict[str, Any],
    artifact_store: InMemoryArtifactStore | None = None,
    summarizer: Summarizer | None = None,
    extractor: Extractor | None = None,
) -> ResultEnvelope:
    """MCP tool result -> ResultEnvelope."""
    is_error = mcp_result.get("isError", False)
    content_list = mcp_result.get("content", [])

    # Concatenate text content
    texts = []
    for part in content_list:
        if isinstance(part, dict) and part.get("type") == "text":
            texts.append(part.get("text", ""))
        elif isinstance(part, str):
            texts.append(part)
    full_text = "\n".join(texts) if texts else str(mcp_result)

    status: Literal["ok", "partial", "error"] = "error" if is_error else "ok"
    summ = summarizer or RuleBasedSummarizer()
    ext = extractor or StructuredExtractor()

    summary = summ.summarize(full_text)
    facts = ext.extract(full_text)

    artifacts: list[ArtifactRef] = []
    if artifact_store is not None and len(full_text) > 2000:
        handle = f"mcp_result_{id(mcp_result)}"
        import asyncio

        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop and loop.is_running():
            import concurrent.futures

            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(asyncio.run, artifact_store.put(handle, full_text)).result()
        else:
            asyncio.run(artifact_store.put(handle, full_text))
        artifacts.append(
            ArtifactRef(
                handle=handle,
                media_type="text/plain",
                size_bytes=len(full_text),
                label="MCP tool result",
            )
        )

    return ResultEnvelope(
        status=status,
        summary=summary,
        facts=facts,
        artifacts=artifacts,
        provenance={"source": "mcp"},
    )


def load_mcp_session_jsonl(path: str | Path) -> list[ContextItem]:
    """Load JSONL of MCP events -> ContextItems.

    Links tool_results to tool_calls via parent_id.
    """
    items: list[ContextItem] = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            data = json.loads(line)
            item = _mcp_line_to_item(data)
            if item:
                items.append(item)
    return items


def _mcp_line_to_item(data: dict[str, Any]) -> ContextItem | None:
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

    if event_type == "user_turn":
        text = data.get("text", "")
    elif event_type == "tool_call":
        text = f"Call {data.get('tool_name', '')}: {json.dumps(data.get('args', {}))}"
    elif event_type == "tool_result":
        text = data.get("content", "")
    elif event_type == "agent_msg":
        text = data.get("text", "")
    else:
        text = str(data)

    parent_id = data.get("tool_call_id") if event_type == "tool_result" else None

    return ContextItem(
        id=item_id,
        kind=kind,
        text=text,
        token_estimate=len(text) // 4,
        metadata={"timestamp": timestamp, "source": "mcp"},
        parent_id=parent_id,
    )

"""A2A (Agent-to-Agent) adapter for contextweaver.

Converts A2A agent descriptors into
:class:`~contextweaver.types.SelectableItem` objects and wraps A2A task
results as :class:`~contextweaver.envelope.ResultEnvelope` instances.

Also provides :func:`load_a2a_session_jsonl` for replaying A2A sessions from
JSONL files into contextweaver :class:`~contextweaver.types.ContextItem` lists.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from contextweaver.envelope import ResultEnvelope
from contextweaver.exceptions import CatalogError
from contextweaver.types import ArtifactRef, ContextItem, ItemKind, SelectableItem


def a2a_agent_to_selectable(agent_card: dict[str, Any]) -> SelectableItem:
    """Convert an A2A agent card dict to a :class:`SelectableItem`.

    Expected keys:

    - ``name`` (required)
    - ``description`` (required)
    - ``skills`` (optional list of skill dicts with ``id``, ``name``,
      ``description``)
    - ``defaultInputModes`` (optional list, e.g. ``["text/plain"]``)
    - ``defaultOutputModes`` (optional list)
    - ``url`` (optional agent endpoint)

    Args:
        agent_card: Raw A2A agent card as returned by the
            ``/.well-known/agent.json`` endpoint.

    Returns:
        A :class:`SelectableItem` with ``kind="agent"`` and
        ``namespace="a2a"``.

    Raises:
        CatalogError: If required fields are missing.
    """
    name = agent_card.get("name")
    description = agent_card.get("description")
    if not name or not description:
        missing: list[str] = []
        if not name:
            missing.append("name")
        if not description:
            missing.append("description")
        raise CatalogError(f"A2A agent card missing required fields: {missing}")

    skills: list[dict[str, Any]] = agent_card.get("skills") or []
    tags: list[str] = ["a2a"]
    for skill in skills:
        skill_name = skill.get("name", "")
        if skill_name:
            tags.append(skill_name)

    input_modes: list[str] = agent_card.get("defaultInputModes") or []
    output_modes: list[str] = agent_card.get("defaultOutputModes") or []

    return SelectableItem(
        id=f"a2a:{name}",
        kind="agent",
        name=str(name),
        description=str(description),
        tags=sorted(set(tags)),
        namespace="a2a",
        args_schema={},
        side_effects=False,
        cost_hint=0.0,
        metadata={
            "skills": skills,
            "input_modes": input_modes,
            "output_modes": output_modes,
            "url": agent_card.get("url", ""),
        },
    )


def a2a_result_to_envelope(
    task_result: dict[str, Any],
    agent_name: str,
) -> ResultEnvelope:
    """Convert an A2A task result to a :class:`ResultEnvelope`.

    The A2A task result is expected to have:

    - ``status`` — dict with ``state`` (e.g. ``"completed"``, ``"failed"``)
      and optional ``message``.
    - ``artifacts`` — optional list of artifact dicts, each with ``parts``
      (list of ``{"type": ..., "text": ...}`` parts).
    - ``history`` — optional list of message dicts.

    .. note::

        Returned :class:`~contextweaver.types.ArtifactRef` entries are
        **metadata-only** — the underlying data is not persisted to an
        :class:`~contextweaver.protocols.ArtifactStore`.  Callers that
        need resolvable handles should store the raw data separately
        (e.g. via :meth:`ContextManager.ingest_tool_result`).

    Args:
        task_result: Raw A2A task result dict.
        agent_name: The name of the agent that produced the result.

    Returns:
        A :class:`ResultEnvelope`.
    """
    status_obj: dict[str, Any] = task_result.get("status") or {}
    state = status_obj.get("state", "unknown")
    status_msg = status_obj.get("message", "")

    # Map A2A states to envelope statuses
    from typing import Literal

    env_status: Literal["ok", "partial", "error"]
    if state in ("completed",):
        env_status = "ok"
    elif state in ("failed", "rejected"):
        env_status = "error"
    else:
        env_status = "partial"

    # Extract text from artifacts
    text_parts: list[str] = []
    artifact_refs: list[ArtifactRef] = []
    a2a_artifacts: list[dict[str, Any]] = task_result.get("artifacts") or []

    for i, artifact in enumerate(a2a_artifacts):
        parts: list[dict[str, Any]] = artifact.get("parts") or []
        for part in parts:
            part_type = part.get("type", "text")
            if part_type == "text":
                text_parts.append(part.get("text", ""))
            elif part_type == "data":
                mime = part.get("mimeType", "application/octet-stream")
                data_str = part.get("data", "")
                artifact_refs.append(
                    ArtifactRef(
                        handle=f"a2a:{agent_name}:artifact:{i}",
                        media_type=mime,
                        size_bytes=len(data_str),
                        label=f"artifact from {agent_name}",
                    )
                )

    summary_parts = list(text_parts)
    if status_msg and not text_parts:
        summary_parts.append(status_msg)
    summary = "\n".join(summary_parts) if summary_parts else f"({state})"

    # Extract simple facts
    facts: list[str] = [f"state: {state}"]
    if status_msg:
        facts.append(f"message: {status_msg}")
    for part_text in text_parts:
        for line in part_text.splitlines():
            stripped = line.strip()
            if ":" in stripped and len(stripped) < 200:
                facts.append(stripped)

    return ResultEnvelope(
        status=env_status,
        summary=summary[:500] if len(summary) > 500 else summary,
        facts=facts[:20],
        artifacts=artifact_refs,
        provenance={"agent": agent_name, "protocol": "a2a", "state": state},
    )


def load_a2a_session_jsonl(path: str | Path) -> list[ContextItem]:
    """Load an A2A session from a JSONL file into a list of ContextItems.

    Each line must be a JSON object with at least:

    - ``type``: one of ``"user_turn"``, ``"agent_msg"``, ``"tool_call"``,
      ``"tool_result"``
    - ``id``: unique string identifier
    - ``text`` or ``content``: the textual content

    Agent messages are linked to user turns via ``parent_id`` when present.

    Args:
        path: Filesystem path to a JSONL file.

    Returns:
        A list of :class:`ContextItem` in file order.

    Raises:
        CatalogError: If the file cannot be read or contains invalid lines.
    """
    try:
        lines = Path(path).read_text(encoding="utf-8").strip().splitlines()
    except OSError as exc:
        raise CatalogError(f"Cannot read A2A session file: {exc}") from exc

    items: list[ContextItem] = []
    kind_map: dict[str, ItemKind] = {
        "tool_call": ItemKind.tool_call,
        "tool_result": ItemKind.tool_result,
        "user_turn": ItemKind.user_turn,
        "agent_msg": ItemKind.agent_msg,
        "doc_snippet": ItemKind.doc_snippet,
        "memory_fact": ItemKind.memory_fact,
        "plan_state": ItemKind.plan_state,
        "policy": ItemKind.policy,
    }

    for lineno, line in enumerate(lines, 1):
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CatalogError(f"Invalid JSON at line {lineno}: {exc}") from exc

        if not isinstance(obj, dict):
            raise CatalogError(f"Expected JSON object at line {lineno}, got {type(obj).__name__}")

        try:
            kind_str = obj.get("type", "agent_msg")
            kind = kind_map.get(kind_str, ItemKind.agent_msg)
            text = obj.get("text") or obj.get("content", "")

            items.append(
                ContextItem(
                    id=obj.get("id", f"a2a-line-{lineno}"),
                    kind=kind,
                    text=str(text),
                    token_estimate=int(obj.get("token_estimate", 0)),
                    metadata=dict(obj.get("metadata", {})),
                    parent_id=obj.get("parent_id"),
                )
            )
        except (TypeError, ValueError, AttributeError) as exc:
            raise CatalogError(f"Invalid context item at line {lineno}: {exc}") from exc

    return items

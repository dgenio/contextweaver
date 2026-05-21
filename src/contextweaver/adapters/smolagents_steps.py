"""smolagents step-log adapter for contextweaver (issue #274).

Pure stateless converter between a
[smolagents](https://github.com/huggingface/smolagents) ``MultiStepAgent``
step memory and contextweaver's :class:`~contextweaver.types.ContextItem`
event log.

smolagents records every reasoning step under
``MultiStepAgent.memory.steps``.  Each step (typed as ``ActionStep`` /
``PlanningStep`` / ``TaskStep`` / ``SystemPromptStep`` / ``FinalAnswerStep``
in the upstream API, but exposed as ``.model_dump()``-style dicts here)
nests a *thought* (``model_output``) and an optional *tool call* with
its observation.  The decoder flattens those into one or two
``ContextItem``\\ s per step.

.. code-block:: python

    from contextweaver.adapters.smolagents_steps import from_smolagents_agent

    items = from_smolagents_agent(agent, into=mgr)

The ``agent`` argument accepts either a live
``smolagents.MultiStepAgent`` (anything exposing
``.memory.steps`` or ``.memory.get_full_steps()``) or an explicit
``list[dict]`` for tests / fixtures.  No ``smolagents`` import is
required for the dict path.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from contextweaver.adapters._messages_common import (
    expect_dict,
    expect_list,
    ingest_into_manager,
    json_args_dumps,
)
from contextweaver.exceptions import CatalogError
from contextweaver.types import ContextItem, ItemKind

if TYPE_CHECKING:
    from contextweaver.context.manager import ContextManager

logger = logging.getLogger("contextweaver.adapters")

_PREFIX_TASK = "smolagents:task:"
_PREFIX_SYSTEM = "smolagents:system:"
_PREFIX_PLANNING = "smolagents:plan:"
_PREFIX_THOUGHT = "smolagents:thought:"
_PREFIX_TOOL_CALL = "smolagents:tool_call:"
_PREFIX_OBSERVATION = "smolagents:observation:"
_PREFIX_FINAL = "smolagents:final:"


def _extract_steps(agent: Any) -> list[dict[str, Any]]:  # noqa: ANN401 — provider object
    """Pull a list of step dicts out of either a live agent or a raw list."""
    if isinstance(agent, list):
        return [_step_dump(s) for s in agent]
    memory = getattr(agent, "memory", None)
    if memory is None:
        raise CatalogError(f"smolagents agent {agent!r} has no .memory attribute")
    full_steps_fn = getattr(memory, "get_full_steps", None)
    if callable(full_steps_fn):
        try:
            raw = full_steps_fn()
        except Exception as exc:  # pragma: no cover - defensive
            raise CatalogError(f"smolagents memory.get_full_steps() failed: {exc}") from exc
        if isinstance(raw, list):
            return [_step_dump(s) for s in raw]
    steps = getattr(memory, "steps", None)
    if isinstance(steps, list):
        return [_step_dump(s) for s in steps]
    raise CatalogError(f"smolagents memory {memory!r} has neither .steps nor .get_full_steps()")


def _step_dump(value: Any) -> dict[str, Any]:  # noqa: ANN401 — provider object
    if isinstance(value, dict):
        return value
    dump_fn = getattr(value, "model_dump", None) or getattr(value, "dict", None)
    if callable(dump_fn):
        try:
            dumped = dump_fn()
        except Exception as exc:  # pragma: no cover - defensive
            raise CatalogError(f"smolagents step {value!r} failed to dump: {exc}") from exc
        if isinstance(dumped, dict):
            return dumped
    # Fall back to attribute-scrape for objects without model_dump (e.g.
    # plain dataclasses used in older smolagents releases).
    fields = ("task", "model_output", "tool_calls", "observations", "final_answer", "plan")
    scraped = {k: getattr(value, k) for k in fields if hasattr(value, k)}
    if scraped:
        return scraped
    raise CatalogError(f"smolagents step {value!r} is not dict-convertible")


def from_smolagents_agent(
    agent: Any,  # noqa: ANN401 — accepts live MultiStepAgent or list[dict]
    into: ContextManager | None = None,
) -> list[ContextItem]:
    """Convert smolagents step memory into ContextItems.

    Args:
        agent: A live ``smolagents.MultiStepAgent`` (anything exposing
            ``.memory.steps`` / ``.memory.get_full_steps()``) or an
            explicit ``list[dict]`` of step dumps.
        into: Optional :class:`ContextManager`.  When provided, every
            produced item is appended to the manager's event log.

    Returns:
        A list of :class:`ContextItem` in step order.  Each step expands
        to between one and three items depending on its kind: a task /
        system step becomes one ``user_turn`` / ``policy`` item; an
        action step becomes one ``agent_msg`` (thought) followed by zero
        or more ``tool_call`` + ``tool_result`` pairs.

    Raises:
        CatalogError: On malformed input.
    """
    steps = _extract_steps(agent)
    expect_list(steps, fn_name="from_smolagents_agent")

    items: list[ContextItem] = []
    seen_tool_call_ids: set[str] = set()
    for step_idx, raw in enumerate(steps):
        expect_dict(raw, label=f"smolagents step at index {step_idx}")
        items.extend(_step_to_items(raw, step_idx, seen_tool_call_ids))

    ingest_into_manager(items, into)
    logger.debug("from_smolagents_agent: steps_in=%d, items_out=%d", len(steps), len(items))
    return items


def _step_to_items(
    step: dict[str, Any],
    step_idx: int,
    seen_tool_call_ids: set[str],
) -> list[ContextItem]:
    out: list[ContextItem] = []
    meta_base: dict[str, Any] = {"step_index": step_idx}

    task = step.get("task")
    if isinstance(task, str) and task:
        out.append(
            ContextItem(
                id=f"{_PREFIX_TASK}{step_idx}",
                kind=ItemKind.user_turn,
                text=task,
                metadata={**meta_base, "step_kind": "task"},
            )
        )
        return out

    system_prompt = step.get("system_prompt")
    if isinstance(system_prompt, str) and system_prompt:
        out.append(
            ContextItem(
                id=f"{_PREFIX_SYSTEM}{step_idx}",
                kind=ItemKind.policy,
                text=system_prompt,
                metadata={**meta_base, "step_kind": "system"},
            )
        )
        return out

    plan = step.get("plan")
    if isinstance(plan, str) and plan:
        out.append(
            ContextItem(
                id=f"{_PREFIX_PLANNING}{step_idx}",
                kind=ItemKind.plan_state,
                text=plan,
                metadata={**meta_base, "step_kind": "planning"},
            )
        )
        return out

    final_answer = step.get("final_answer")
    if final_answer is not None:
        out.append(
            ContextItem(
                id=f"{_PREFIX_FINAL}{step_idx}",
                kind=ItemKind.agent_msg,
                text=str(final_answer),
                metadata={**meta_base, "step_kind": "final"},
            )
        )
        return out

    thought = step.get("model_output") or step.get("thought") or ""
    if isinstance(thought, str) and thought:
        out.append(
            ContextItem(
                id=f"{_PREFIX_THOUGHT}{step_idx}",
                kind=ItemKind.agent_msg,
                text=thought,
                metadata={**meta_base, "step_kind": "action"},
            )
        )

    tool_calls = step.get("tool_calls") or []
    observations = step.get("observations")
    if isinstance(tool_calls, list):
        for call_idx, tc in enumerate(tool_calls):
            expect_dict(tc, label=f"smolagents tool_call at step {step_idx} call {call_idx}")
            out.extend(
                _tool_call_to_items(
                    tc,
                    step_idx,
                    call_idx,
                    observations,
                    seen_tool_call_ids,
                )
            )
    return out


def _tool_call_to_items(
    tc: dict[str, Any],
    step_idx: int,
    call_idx: int,
    observations: object,
    seen_tool_call_ids: set[str],
) -> list[ContextItem]:
    tool_call_id = str(tc.get("id") or tc.get("tool_call_id") or f"{step_idx}:{call_idx}")
    tool_name = str(tc.get("name") or tc.get("tool_name") or "")
    args_payload = tc.get("arguments")
    if args_payload is None:
        args_payload = tc.get("args")
    label = f"smolagents tool_call at step {step_idx} call {call_idx}"
    args_str = json_args_dumps(args_payload, label=label) if args_payload is not None else ""
    seen_tool_call_ids.add(tool_call_id)

    out: list[ContextItem] = [
        ContextItem(
            id=f"{_PREFIX_TOOL_CALL}{tool_call_id}",
            kind=ItemKind.tool_call,
            text=f"{tool_name}({args_str})",
            metadata={
                "step_index": step_idx,
                "call_index": call_idx,
                "tool_call_id": tool_call_id,
                "tool_name": tool_name,
                "args": args_str,
            },
        )
    ]
    if isinstance(observations, str) and observations:
        out.append(
            ContextItem(
                id=f"{_PREFIX_OBSERVATION}{tool_call_id}",
                kind=ItemKind.tool_result,
                text=observations,
                metadata={
                    "step_index": step_idx,
                    "call_index": call_idx,
                    "tool_call_id": tool_call_id,
                    "tool_name": tool_name,
                },
                parent_id=f"{_PREFIX_TOOL_CALL}{tool_call_id}",
            )
        )
    return out

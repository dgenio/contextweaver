"""smolagents adapter for contextweaver (issue #274).

Bridges Hugging Face's [smolagents](https://github.com/huggingface/smolagents)
``Tool`` definitions and ``MultiStepAgent`` step logs to contextweaver-native
types.  Converts smolagents ``Tool`` subclasses (or the equivalent plain-dict
shape that ``Tool.to_dict()`` emits) into :class:`SelectableItem` objects so
agents built on ``CodeAgent`` / ``ToolCallingAgent`` can route through
contextweaver's bounded-choice router instead of dumping every tool definition
into the system prompt.

Two surfaces:

1. **Tool catalog** — :func:`smolagents_tool_to_selectable`,
   :func:`smolagents_tools_to_catalog`, :func:`load_smolagents_catalog`.
   Mirrors :mod:`.crewai` (issue #193).

2. **Step ingestion** — :func:`from_smolagents_agent` reads a
   ``MultiStepAgent.memory.steps`` log (or any iterable of equivalent step
   dicts) and produces :class:`ContextItem`s representing the executed
   tool calls, their results, and the final answer.  Generated code from
   ``CodeAgent`` is translated only via the *executed* tool calls, not
   the raw code blocks — per the acceptance criteria of #274.

The plain-dict / step-dict paths work without the ``smolagents`` package
installed; the live :func:`load_smolagents_catalog` and
:func:`from_smolagents_agent` helpers accept real smolagents objects when
the ``contextweaver[smolagents]`` optional extra is installed.

smolagents docs:  https://huggingface.co/docs/smolagents
"""

from __future__ import annotations

import copy
import logging
from typing import TYPE_CHECKING, Any

from contextweaver.adapters._messages_common import (
    expect_dict,
    expect_list,
    ingest_into_manager,
    json_args_dumps,
)
from contextweaver.exceptions import CatalogError
from contextweaver.routing.catalog import Catalog
from contextweaver.types import ContextItem, ItemKind, SelectableItem

if TYPE_CHECKING:
    from contextweaver.context.manager import ContextManager

logger = logging.getLogger("contextweaver.adapters")

_FALLBACK_NS = "smolagents"
_ID_PREFIX = "smolagents"


# ---------------------------------------------------------------------------
# Tool → SelectableItem
# ---------------------------------------------------------------------------


def infer_smolagents_namespace(tool_name: str) -> str:
    """Infer a namespace from a smolagents tool name.

    smolagents tools are commonly named in snake_case with an optional
    underscore-prefixed namespace (e.g. ``web_search``, ``image_generator``).
    Falls back to ``"smolagents"`` when no prefix can be detected.

    Args:
        tool_name: The raw tool name string.

    Returns:
        The inferred namespace string.
    """
    if not tool_name:
        return _FALLBACK_NS
    for sep in (".", "/"):
        if sep in tool_name:
            prefix = tool_name.split(sep, 1)[0]
            if prefix:
                return prefix
    parts = tool_name.split("_")
    if len(parts) >= 2 and parts[0] and not parts[0].startswith("_"):
        return parts[0]
    return _FALLBACK_NS


def _strip_namespace_prefix(tool_name: str, namespace: str) -> str:
    """Return the short tool name with the namespace prefix removed."""
    for prefix in (f"{namespace}_", f"{namespace}.", f"{namespace}/"):
        if tool_name.startswith(prefix) and len(tool_name) > len(prefix):
            return tool_name[len(prefix) :]
    return tool_name


def _build_args_schema(inputs: object, output_type: object) -> dict[str, Any]:
    """Build a JSON-Schema dict from smolagents' ``inputs`` + ``output_type``.

    smolagents' ``Tool`` class declares arguments via an ``inputs`` mapping
    shaped like ``{arg_name: {"type": "string", "description": "..."}}``;
    the output type is a string like ``"string"`` or ``"image"``.  We
    coerce that to the JSON-Schema ``properties`` + ``required`` shape so
    the converted :class:`SelectableItem` has a contextweaver-native
    ``args_schema``.

    Args:
        inputs: The raw ``Tool.inputs`` mapping (or ``None``).
        output_type: The raw ``Tool.output_type`` string (or ``None``).

    Returns:
        A JSON-Schema dict.  Empty when ``inputs`` is missing or not a dict.
    """
    if not isinstance(inputs, dict):
        return {}
    properties: dict[str, Any] = {}
    required: list[str] = []
    for arg_name, spec in inputs.items():
        if not isinstance(arg_name, str):
            continue
        if isinstance(spec, dict):
            entry = copy.deepcopy(spec)
            # smolagents uses ``nullable=True`` to indicate optional args.
            is_optional = bool(entry.pop("nullable", False))
            properties[arg_name] = entry
            if not is_optional:
                required.append(arg_name)
        else:
            properties[arg_name] = {"type": "string"}
            required.append(arg_name)
    schema: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        schema["required"] = sorted(required)
    if isinstance(output_type, str) and output_type:
        schema["x-smolagents-output-type"] = output_type
    return schema


def smolagents_tool_to_selectable(
    tool_def: dict[str, Any],
    *,
    namespace: str | None = None,
) -> SelectableItem:
    """Convert a smolagents tool definition dict to a :class:`SelectableItem`.

    The dict shape mirrors the field set on ``smolagents.Tool`` (which is
    a plain Python class — :func:`load_smolagents_catalog` reads the
    instance attributes and constructs the equivalent dict):

    - ``name`` (required): the tool's display name.
    - ``description`` (required): natural-language description for the LLM.
    - ``inputs`` (optional): ``{arg_name: {type, description, nullable?}}``.
    - ``output_type`` (optional): smolagents output type string.

    Args:
        tool_def: Raw tool definition dict.
        namespace: Explicit namespace override.  When ``None``, the
            namespace is inferred from the tool name via
            :func:`infer_smolagents_namespace`.

    Returns:
        A :class:`SelectableItem` with ``kind="tool"`` and an ``id`` of
        ``"smolagents:{name}"``.

    Raises:
        CatalogError: If required fields (``name``, ``description``) are
            missing or non-string.
    """
    raw_name = tool_def.get("name")
    if not isinstance(raw_name, str) or not raw_name:
        raise CatalogError("smolagents tool definition is missing a non-empty 'name' field.")
    raw_description = tool_def.get("description")
    if not isinstance(raw_description, str) or not raw_description:
        raise CatalogError(
            f"smolagents tool {raw_name!r} is missing a non-empty 'description' field."
        )

    ns = namespace if namespace is not None else infer_smolagents_namespace(raw_name)
    short_name = _strip_namespace_prefix(raw_name, ns)

    args_schema = _build_args_schema(tool_def.get("inputs"), tool_def.get("output_type"))

    raw_tags = tool_def.get("tags")
    tags: set[str] = {_FALLBACK_NS}
    if isinstance(raw_tags, (list, set, tuple)):
        for tag in raw_tags:
            if isinstance(tag, str) and tag:
                tags.add(tag)

    metadata: dict[str, Any] = {}
    if "output_type" in tool_def and isinstance(tool_def["output_type"], str):
        metadata["output_type"] = tool_def["output_type"]
    if "is_initialized" in tool_def:
        metadata["is_initialized"] = bool(tool_def["is_initialized"])

    logger.debug(
        "smolagents_tool_to_selectable: name=%s, ns=%s, tags=%s",
        raw_name,
        ns,
        sorted(tags),
    )
    return SelectableItem(
        id=f"{_ID_PREFIX}:{raw_name}",
        kind="tool",
        name=short_name,
        description=raw_description,
        tags=sorted(tags),
        namespace=ns,
        args_schema=args_schema,
        metadata=metadata,
    )


# Alias matching the issue #274 spelling.
selectable_from_smolagents_tool = smolagents_tool_to_selectable


def smolagents_tools_to_catalog(
    tools: list[dict[str, Any]],
    *,
    namespace: str | None = None,
) -> Catalog:
    """Convert a list of smolagents tool definitions to a populated :class:`Catalog`.

    Args:
        tools: List of raw tool definition dicts.
        namespace: Optional namespace override applied to every item.

    Returns:
        A populated :class:`~contextweaver.routing.catalog.Catalog`.

    Raises:
        CatalogError: If a tool definition is invalid or duplicate IDs are
            encountered.
    """
    catalog = Catalog()
    for tool_def in tools:
        catalog.register(smolagents_tool_to_selectable(tool_def, namespace=namespace))
    logger.debug("smolagents_tools_to_catalog: registered %d items", len(tools))
    return catalog


def load_smolagents_catalog(
    tools: list[object],
    *,
    namespace: str | None = None,
) -> Catalog:
    """Convert a list of live ``smolagents.Tool`` instances to a :class:`Catalog`.

    smolagents' ``Tool`` class exposes ``name``, ``description``,
    ``inputs``, and ``output_type`` as class-level attributes.  This helper
    reads them off the instance and constructs the equivalent dict for
    :func:`smolagents_tools_to_catalog`.

    Args:
        tools: List of live smolagents ``Tool`` instances (or any object
            exposing ``name`` / ``description`` attributes).
        namespace: Optional namespace override applied to every item.

    Returns:
        A populated :class:`Catalog`.

    Raises:
        CatalogError: If a tool object is missing required attributes.
    """
    tool_dicts: list[dict[str, Any]] = []
    for tool in tools:
        name = getattr(tool, "name", None)
        description = getattr(tool, "description", None)
        if not isinstance(name, str) or not name:
            raise CatalogError(f"smolagents tool {tool!r} is missing a non-empty 'name' attribute.")
        if not isinstance(description, str) or not description:
            raise CatalogError(
                f"smolagents tool {name!r} is missing a non-empty 'description' attribute."
            )
        tool_dicts.append(
            {
                "name": name,
                "description": description,
                "inputs": getattr(tool, "inputs", None),
                "output_type": getattr(tool, "output_type", None),
                "tags": list(getattr(tool, "tags", []) or []),
            }
        )
    return smolagents_tools_to_catalog(tool_dicts, namespace=namespace)


# ---------------------------------------------------------------------------
# Step ingestion
# ---------------------------------------------------------------------------


def _step_to_dict(step: object) -> dict[str, Any]:
    """Coerce a smolagents step object to a plain dict.

    smolagents represents steps as Pydantic-ish dataclasses (``TaskStep``,
    ``ActionStep``, ``PlanningStep``, ``FinalAnswerStep``).  They typically
    expose ``to_dict`` / ``model_dump``; fall back to ``vars(step)`` when
    neither is available.
    """
    if isinstance(step, dict):
        return dict(step)
    for fn_name in ("to_dict", "model_dump"):
        fn = getattr(step, fn_name, None)
        if callable(fn):
            try:
                dumped = fn()
            except Exception as exc:  # pragma: no cover - defensive
                raise CatalogError(f"smolagents step {step!r}.{fn_name}() raised: {exc}") from exc
            if isinstance(dumped, dict):
                return dumped
    try:
        return dict(vars(step))
    except TypeError as exc:
        raise CatalogError(
            f"smolagents step {step!r} is neither a dict nor has dumpable attributes."
        ) from exc


def _classify_step(step_data: dict[str, Any]) -> str:
    """Return a normalised step-type tag."""
    explicit = step_data.get("step_type") or step_data.get("type")
    if isinstance(explicit, str):
        return explicit
    if "task" in step_data and "tool_calls" not in step_data:
        return "task"
    if "final_answer" in step_data:
        return "final_answer"
    if "tool_calls" in step_data or "tool_call" in step_data:
        return "action"
    if "facts" in step_data or "plan" in step_data:
        return "planning"
    return "action"


def from_smolagents_agent(
    agent_or_steps: object,
    into: ContextManager | None = None,
) -> list[ContextItem]:
    """Convert a smolagents agent / step log into :class:`ContextItem`s.

    Accepts either:

    - A live ``smolagents.MultiStepAgent`` instance (reads ``agent.memory.steps``).
    - A plain ``list`` of step dicts / step objects following the
      ``TaskStep`` / ``ActionStep`` / ``FinalAnswerStep`` shape.

    Mapping rules:

    - ``TaskStep`` → :data:`ItemKind.user_turn` (one item per task).
    - ``ActionStep.tool_calls`` → one :data:`ItemKind.tool_call` per call.
    - ``ActionStep.observations`` → one :data:`ItemKind.tool_result`
      with ``parent_id`` set to the originating ``tool_call_id``.
    - ``ActionStep.model_output`` (free-text reasoning) → :data:`ItemKind.agent_msg`.
    - ``FinalAnswerStep`` → :data:`ItemKind.agent_msg` with
      ``metadata["final_answer"] = True``.

    Code blocks emitted by ``CodeAgent`` are **not** translated as
    ``ContextItem``s — only the executed tool calls and observations are
    surfaced.  This matches the #274 acceptance criterion that the adapter
    should translate executed calls, not generated code.

    Args:
        agent_or_steps: A live ``MultiStepAgent`` instance, or a list of
            step dicts/objects.
        into: Optional :class:`~contextweaver.context.manager.ContextManager`
            to append items to.

    Returns:
        A list of :class:`ContextItem` in step order.

    Raises:
        CatalogError: On malformed step entries.
    """
    if isinstance(agent_or_steps, list):
        steps_iterable: list[object] = agent_or_steps
    else:
        memory = getattr(agent_or_steps, "memory", None)
        steps = getattr(memory, "steps", None) if memory is not None else None
        if steps is None:
            steps = getattr(agent_or_steps, "steps", None)
        if steps is None:
            raise CatalogError(
                "from_smolagents_agent could not locate a 'memory.steps' or 'steps' "
                "iterable on the input object."
            )
        steps_iterable = list(steps)

    items: list[ContextItem] = []
    for idx, step in enumerate(steps_iterable):
        step_data = _step_to_dict(step)
        kind_tag = _classify_step(step_data)
        if kind_tag == "task":
            task_text = step_data.get("task") or step_data.get("text") or ""
            if not isinstance(task_text, str):
                task_text = str(task_text)
            items.append(
                ContextItem(
                    id=f"{_ID_PREFIX}:task:{idx}",
                    kind=ItemKind.user_turn,
                    text=task_text,
                    metadata={
                        "step_index": idx,
                        "provider": _ID_PREFIX,
                        "step_type": "task",
                    },
                )
            )
        elif kind_tag == "final_answer":
            answer = step_data.get("final_answer", step_data.get("answer", ""))
            if not isinstance(answer, str):
                answer = json_args_dumps(answer, label=f"final_answer step {idx}")
            items.append(
                ContextItem(
                    id=f"{_ID_PREFIX}:final:{idx}",
                    kind=ItemKind.agent_msg,
                    text=answer,
                    metadata={
                        "step_index": idx,
                        "provider": _ID_PREFIX,
                        "step_type": "final_answer",
                        "final_answer": True,
                    },
                )
            )
        elif kind_tag == "planning":
            plan = step_data.get("plan") or step_data.get("facts") or ""
            if not isinstance(plan, str):
                plan = json_args_dumps(plan, label=f"planning step {idx}")
            items.append(
                ContextItem(
                    id=f"{_ID_PREFIX}:plan:{idx}",
                    kind=ItemKind.plan_state,
                    text=plan,
                    metadata={
                        "step_index": idx,
                        "provider": _ID_PREFIX,
                        "step_type": "planning",
                    },
                )
            )
        else:
            items.extend(_decode_action_step(idx, step_data))

    ingest_into_manager(items, into)
    return items


def _decode_action_step(idx: int, step_data: dict[str, Any]) -> list[ContextItem]:
    """Decode a smolagents ``ActionStep`` into :class:`ContextItem`s."""
    items: list[ContextItem] = []
    model_output = step_data.get("model_output")
    if isinstance(model_output, str) and model_output.strip():
        items.append(
            ContextItem(
                id=f"{_ID_PREFIX}:reasoning:{idx}",
                kind=ItemKind.agent_msg,
                text=model_output,
                metadata={
                    "step_index": idx,
                    "provider": _ID_PREFIX,
                    "step_type": "reasoning",
                },
            )
        )

    raw_calls = step_data.get("tool_calls")
    if raw_calls is None and "tool_call" in step_data:
        raw_calls = [step_data["tool_call"]]
    if isinstance(raw_calls, list):
        expect_list(raw_calls, fn_name="from_smolagents_agent")
        for call_idx, call in enumerate(raw_calls):
            expect_dict(call, label=f"smolagents step {idx} tool_call {call_idx}")
            call_id = (
                call.get("id") or call.get("tool_call_id") or f"{_ID_PREFIX}-call-{idx}-{call_idx}"
            )
            if not isinstance(call_id, str):
                call_id = str(call_id)
            tool_name = call.get("name") or call.get("tool_name") or ""
            args_payload = call.get("arguments", call.get("args", {}))
            args_text = json_args_dumps(args_payload, label=f"smolagents tool_call {call_id!r}")
            items.append(
                ContextItem(
                    id=f"{_ID_PREFIX}:tool_call:{call_id}",
                    kind=ItemKind.tool_call,
                    text=args_text,
                    metadata={
                        "step_index": idx,
                        "provider": _ID_PREFIX,
                        "step_type": "action",
                        "tool_name": tool_name,
                        "tool_call_id": call_id,
                    },
                )
            )

    observations = step_data.get("observations")
    if observations is None:
        observations = step_data.get("observation")
    if observations is not None:
        if not isinstance(observations, str):
            observations = json_args_dumps(observations, label=f"smolagents observation step {idx}")
        # Pair observation with the last tool call if any.
        last_tool_call: ContextItem | None = next(
            (item for item in reversed(items) if item.kind is ItemKind.tool_call),
            None,
        )
        parent_id = last_tool_call.id if last_tool_call else None
        tool_name = last_tool_call.metadata.get("tool_name") if last_tool_call is not None else ""
        tool_call_id = (
            last_tool_call.metadata.get("tool_call_id") if last_tool_call is not None else ""
        )
        items.append(
            ContextItem(
                id=f"{_ID_PREFIX}:observation:{idx}",
                kind=ItemKind.tool_result,
                text=observations,
                parent_id=parent_id,
                metadata={
                    "step_index": idx,
                    "provider": _ID_PREFIX,
                    "step_type": "observation",
                    "tool_name": tool_name or "",
                    "tool_call_id": tool_call_id or "",
                },
            )
        )
    return items

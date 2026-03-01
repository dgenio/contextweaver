"""A2A (Agent-to-Agent) adapter for contextweaver.

Provides helpers for converting A2A agent descriptors into
:class:`~contextweaver.types.SelectableItem` objects and wrapping A2A task
results as :class:`~contextweaver.types.ResultEnvelope` instances.
"""

from __future__ import annotations

from typing import Any

from contextweaver.envelope import ResultEnvelope
from contextweaver.types import SelectableItem


def a2a_agent_to_selectable(agent_card: dict[str, Any]) -> SelectableItem:
    """Convert an A2A agent card dict to a :class:`~contextweaver.types.SelectableItem`.

    Expected keys: ``name``, ``description``, ``skills`` (list),
    ``defaultInputModes``, ``defaultOutputModes``.

    Args:
        agent_card: Raw A2A agent card as returned by the ``/.well-known/agent.json``
            endpoint.

    Returns:
        A :class:`~contextweaver.types.SelectableItem` with ``kind="agent"``
        and ``namespace="a2a"``.

    Raises:
        NotImplementedError: Pending implementation — see plan v8.
    """
    raise NotImplementedError("Pending implementation — see plan v8")


def a2a_result_to_envelope(
    task_result: dict[str, Any],
    agent_name: str,
) -> ResultEnvelope:
    """Convert an A2A task result to a :class:`~contextweaver.types.ResultEnvelope`.

    Args:
        task_result: Raw A2A task result dict (``status``, ``artifacts``, etc.).
        agent_name: The name of the agent that produced the result.

    Returns:
        A :class:`~contextweaver.types.ResultEnvelope`.

    Raises:
        NotImplementedError: Pending implementation — see plan v8.
    """
    raise NotImplementedError("Pending implementation — see plan v8")

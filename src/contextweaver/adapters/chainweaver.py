"""ChainWeaver flow-import adapter for contextweaver (issue #334).

[ChainWeaver](https://github.com/dgenio/ChainWeaver) executes deterministic
multi-step *flows*.  This adapter ingests a ChainWeaver **flow export** —
plain data, no ChainWeaver install required — and converts each flow into a
:class:`~contextweaver.types.SelectableItem` with ``kind="flow"`` so a
contextweaver :class:`~contextweaver.routing.router.Router` can shortlist a
flow alongside ordinary tools.  Routing stays *advisory*: contextweaver
selects the flow candidate; a host/runtime (ChainWeaver) executes it.  See
``docs/weaver_spec_mapping.md`` for the route → execute boundary.

Expected flow-export shape
--------------------------

A single flow is a JSON-compatible dict::

    {
        "id": "customer_summary_flow",   # required; "flow_id" also accepted
        "name": "Summarize customer history",   # required
        "description": "Fetch + summarise a customer's recent activity.",  # required
        "version": "1.2.0",              # optional
        "input_schema": {...},           # optional JSON Schema (a.k.a. "inputs")
        "output_schema": {...},          # optional JSON Schema (a.k.a. "outputs")
        "tags": ["customer", "summary"]  # optional
    }

A full export is either a list of such dicts or a dict with a top-level
``"flows"`` list::

    {"flows": [ {...}, {...} ]}

This module is a **pure, stateless converter** (per ``AGENTS.md`` adapter
conventions): it does not import ChainWeaver and performs no I/O.  Callers
that read an export from disk should ``json.load`` it and pass the result to
:func:`load_chainweaver_export`.
"""

from __future__ import annotations

import logging
from typing import Any

from contextweaver.adapters._framework_common import coerce_schema_dict, collect_tags
from contextweaver.exceptions import CatalogError
from contextweaver.routing.catalog import Catalog
from contextweaver.types import SelectableItem

logger = logging.getLogger("contextweaver.adapters")

_FALLBACK_NS = "chainweaver"
#: Tag stamped on every imported flow so callers can gate with
#: ``Router.route(allowed_tags={"flow"})`` or filter flows out explicitly.
FLOW_TAG = "flow"


def chainweaver_flow_to_selectable(
    flow: dict[str, Any],
    *,
    namespace: str | None = None,
) -> SelectableItem:
    """Convert one ChainWeaver flow-export dict to a :class:`SelectableItem`.

    Args:
        flow: A single flow-export dict (see the module docstring for the
            expected shape).  ``id``/``flow_id``, ``name``, and
            ``description`` are required; ``version``, ``input_schema``
            (or ``inputs``), ``output_schema`` (or ``outputs``), and ``tags``
            are optional.
        namespace: Explicit namespace override.  Defaults to
            ``"chainweaver"``.

    Returns:
        A :class:`SelectableItem` with ``kind="flow"``, ``id`` of the form
        ``"chainweaver:{flow_id}"``, ``args_schema`` carrying the flow's
        input schema, and ``output_schema`` carrying its output schema.  The
        flow id, version, and ``runtime="chainweaver"`` are preserved under
        ``metadata`` so a host can resolve the candidate back to a concrete
        ChainWeaver flow.

    Raises:
        CatalogError: If *flow* is not a dict, or a required field is missing
            or non-string.
    """
    if not isinstance(flow, dict):
        raise CatalogError(f"ChainWeaver flow export must be a dict; got {type(flow).__name__}.")
    raw_id = flow.get("id") or flow.get("flow_id")
    if not isinstance(raw_id, str) or not raw_id:
        raise CatalogError(
            "ChainWeaver flow export is missing a non-empty 'id' (or 'flow_id') field."
        )
    raw_name = flow.get("name")
    if not isinstance(raw_name, str) or not raw_name:
        raise CatalogError(f"ChainWeaver flow {raw_id!r} is missing a non-empty 'name' field.")
    raw_description = flow.get("description")
    if not isinstance(raw_description, str) or not raw_description:
        raise CatalogError(
            f"ChainWeaver flow {raw_id!r} is missing a non-empty 'description' field."
        )

    ns = namespace if namespace is not None else _FALLBACK_NS
    args_schema = coerce_schema_dict(flow.get("input_schema", flow.get("inputs")))
    output_schema_raw = coerce_schema_dict(flow.get("output_schema", flow.get("outputs")))
    output_schema = output_schema_raw or None

    tags = collect_tags(flow.get("tags"), fallback=FLOW_TAG)

    metadata: dict[str, Any] = {"runtime": _FALLBACK_NS, "chainweaver_flow_id": raw_id}
    raw_version = flow.get("version")
    if isinstance(raw_version, str) and raw_version:
        metadata["chainweaver_flow_version"] = raw_version

    logger.debug(
        "chainweaver_flow_to_selectable: id=%s, ns=%s, has_input=%s, has_output=%s",
        raw_id,
        ns,
        bool(args_schema),
        output_schema is not None,
    )
    return SelectableItem(
        id=f"{_FALLBACK_NS}:{raw_id}",
        kind="flow",
        name=raw_name,
        description=raw_description,
        tags=sorted(tags),
        namespace=ns,
        args_schema=args_schema,
        output_schema=output_schema,
        metadata=metadata,
    )


def chainweaver_flows_to_catalog(
    flows: list[dict[str, Any]],
    *,
    namespace: str | None = None,
) -> Catalog:
    """Convert a list of ChainWeaver flow-export dicts to a :class:`Catalog`.

    Args:
        flows: List of flow-export dicts.
        namespace: Optional namespace override applied to every flow.

    Returns:
        A :class:`~contextweaver.routing.catalog.Catalog` of ``kind="flow"``
        items.

    Raises:
        CatalogError: If a flow is invalid or duplicate IDs are encountered.
    """
    catalog = Catalog()
    for flow in flows:
        catalog.register(chainweaver_flow_to_selectable(flow, namespace=namespace))
    logger.debug("chainweaver_flows_to_catalog: registered %d flows", len(flows))
    return catalog


def load_chainweaver_export(
    export: list[dict[str, Any]] | dict[str, Any],
    *,
    namespace: str | None = None,
) -> Catalog:
    """Load a full ChainWeaver export into a :class:`Catalog`.

    Accepts either a bare list of flow dicts or a dict with a top-level
    ``"flows"`` list (the two shapes a ChainWeaver export may take).  This is
    the convenience entry point; callers reading from disk should
    ``json.load`` the file first.

    Args:
        export: A list of flow dicts, or ``{"flows": [...]}``.
        namespace: Optional namespace override applied to every flow.

    Returns:
        A populated :class:`Catalog`.

    Raises:
        CatalogError: If *export* is neither a list nor a dict carrying a
            ``"flows"`` list, or if any contained flow is invalid.
    """
    if isinstance(export, dict):
        raw_flows = export.get("flows")
        if not isinstance(raw_flows, list):
            raise CatalogError(
                "ChainWeaver export dict must carry a top-level 'flows' list; "
                f"got keys {sorted(export)}."
            )
        flows = raw_flows
    elif isinstance(export, list):
        flows = export
    else:
        raise CatalogError(
            "ChainWeaver export must be a list of flow dicts or a dict with a 'flows' list; "
            f"got {type(export).__name__}."
        )
    return chainweaver_flows_to_catalog(flows, namespace=namespace)


__all__ = [
    "FLOW_TAG",
    "chainweaver_flow_to_selectable",
    "chainweaver_flows_to_catalog",
    "load_chainweaver_export",
]

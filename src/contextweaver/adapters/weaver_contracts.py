"""weaver-spec contract adapter for contextweaver.

Converts between contextweaver's internal types and the canonical contracts
defined by `weaver-spec <https://github.com/dgenio/weaver-spec>`_:

- :class:`~contextweaver.types.SelectableItem` ↔ ``weaver_contracts.SelectableItem``
- :class:`~contextweaver.envelope.ChoiceCard` ↔ ``weaver_contracts.ChoiceCard``
- :class:`~contextweaver.envelope.RoutingDecision` ↔ ``weaver_contracts.RoutingDecision``
- :class:`~contextweaver.envelope.ResultEnvelope` ↔ ``weaver_contracts.Frame``

Implements issue #143.

Name-clash note:
    The spec uses ``SelectableItem`` for a *menu option* (id/label/description/
    capability_id/metadata) and ``ChoiceCard`` for a *menu of N options*.
    contextweaver uses ``SelectableItem`` for a *full tool definition* (rich
    args/output schemas, examples, tags, etc.) and ``ChoiceCard`` for a *single
    compact LLM-facing card* (1:1).  The adapter preserves the contextweaver
    extras under ``metadata["_contextweaver"]`` so ``cw → spec → cw``
    round-trips losslessly.  See ``docs/weaver_spec_mapping.md``.

Optional dependency:
    The :mod:`weaver_contracts` package is required at *call time* — install
    via ``pip install 'contextweaver[weaver-spec]'``.  Importing this module
    does **not** require ``weaver_contracts``; only calling its public
    functions does.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import ModuleType
from typing import TYPE_CHECKING, Any, cast

from contextweaver.envelope import ChoiceCard, ResultEnvelope, RoutingDecision
from contextweaver.exceptions import CatalogError
from contextweaver.types import ArtifactRef, SelectableItem, ViewSpec

if TYPE_CHECKING:  # pragma: no cover - import-time only
    import weaver_contracts as _ws_types

#: Reserved key under which the adapter stashes contextweaver-specific fields
#: in spec ``metadata`` dicts.  Round-trips read it back; foreign producers can
#: safely ignore it.
_CW_META_KEY = "_contextweaver"

_INSTALL_HINT = (
    "weaver_contracts is not installed. Install with: pip install 'contextweaver[weaver-spec]'"
)


def _import_weaver_contracts() -> ModuleType:
    """Lazy-import the ``weaver_contracts`` package.

    Returns:
        The imported ``weaver_contracts`` module.

    Raises:
        CatalogError: When ``weaver_contracts`` is not installed.
    """
    try:
        import weaver_contracts  # noqa: PLC0415  (lazy by design)
    except ImportError as exc:  # pragma: no cover - exercised by reload test
        raise CatalogError(_INSTALL_HINT) from exc
    # weaver_contracts has no py.typed marker and uses an ignore_missing_imports
    # override (pyproject.toml), so mypy treats the module itself as Any.
    return cast(ModuleType, weaver_contracts)


def _ensure_aware(ts: datetime) -> datetime:
    """Return *ts* with UTC tz-info attached when missing."""
    if ts.tzinfo is None:
        return ts.replace(tzinfo=timezone.utc)
    return ts


# ---------------------------------------------------------------------------
# SelectableItem
# ---------------------------------------------------------------------------


def to_weaver_selectable_item(item: SelectableItem) -> _ws_types.SelectableItem:
    """Convert a contextweaver :class:`SelectableItem` to its weaver-spec form.

    All contextweaver-specific fields (``kind``, ``namespace``, ``args_schema``,
    ``output_schema``, ``examples``, ``constraints``, ``side_effects``,
    ``cost_hint``, ``tags``) are stored under
    ``metadata["_contextweaver"]`` so :func:`from_weaver_selectable_item`
    can rebuild an identical contextweaver item.

    Args:
        item: The contextweaver item to convert.

    Returns:
        A ``weaver_contracts.SelectableItem`` (a menu *option* per the spec).

    Raises:
        CatalogError: When ``weaver_contracts`` is not installed.
    """
    ws = _import_weaver_contracts()
    metadata = dict(item.metadata)
    if _CW_META_KEY in metadata:
        raise CatalogError(
            f"SelectableItem.metadata uses the reserved adapter key {_CW_META_KEY!r}; "
            "rename it before calling to_weaver_selectable_item() so the round-trip "
            "stays lossless."
        )
    cw_extras: dict[str, Any] = {
        "kind": item.kind,
        "tags": list(item.tags),
        "namespace": item.namespace,
        "args_schema": dict(item.args_schema),
        "output_schema": dict(item.output_schema) if item.output_schema is not None else None,
        "examples": list(item.examples),
        "constraints": dict(item.constraints),
        "side_effects": item.side_effects,
        "cost_hint": item.cost_hint,
    }
    metadata[_CW_META_KEY] = cw_extras
    capability_id = f"{item.namespace}:{item.name}" if item.namespace else item.id
    spec_item: Any = ws.SelectableItem(
        id=item.id,
        label=item.name,
        description=item.description,
        capability_id=capability_id,
        metadata=metadata,
    )
    return spec_item


def from_weaver_selectable_item(spec_item: _ws_types.SelectableItem) -> SelectableItem:
    """Convert a weaver-spec ``SelectableItem`` back to a contextweaver one.

    When the spec item carries the adapter's ``metadata["_contextweaver"]``
    payload, the original contextweaver fields are restored exactly.
    Otherwise sensible defaults are used: ``kind="tool"``, empty tags/schemas/
    examples/constraints, ``side_effects=False``, ``cost_hint=0.0``, namespace
    derived from ``capability_id`` when present.
    """
    raw_meta = getattr(spec_item, "metadata", None) or {}
    metadata = dict(raw_meta)
    cw_extras = metadata.pop(_CW_META_KEY, None) or {}
    fallback_ns = ""
    capability_id = getattr(spec_item, "capability_id", None)
    if not cw_extras and isinstance(capability_id, str) and ":" in capability_id:
        fallback_ns = capability_id.split(":", 1)[0]
    raw_output_schema = cw_extras.get("output_schema")
    return SelectableItem(
        id=spec_item.id,
        kind=cw_extras.get("kind", "tool"),
        name=spec_item.label,
        description=spec_item.description,
        tags=list(cw_extras.get("tags", [])),
        namespace=cw_extras.get("namespace", fallback_ns),
        args_schema=dict(cw_extras.get("args_schema", {})),
        output_schema=dict(raw_output_schema) if raw_output_schema is not None else None,
        examples=list(cw_extras.get("examples", [])),
        constraints=dict(cw_extras.get("constraints", {})),
        side_effects=bool(cw_extras.get("side_effects", False)),
        cost_hint=float(cw_extras.get("cost_hint", 0.0)),
        metadata=metadata,
    )


# ---------------------------------------------------------------------------
# ChoiceCard
# ---------------------------------------------------------------------------


def _cw_card_to_spec_option(card: ChoiceCard) -> _ws_types.SelectableItem:
    """Internal: render a contextweaver :class:`ChoiceCard` as a spec menu option."""
    ws = _import_weaver_contracts()
    cw_extras: dict[str, Any] = {
        "kind": card.kind,
        "tags": list(card.tags),
        "namespace": card.namespace,
        "has_schema": card.has_schema,
        "cost_hint": card.cost_hint,
        "side_effects": card.side_effects,
    }
    if card.score is not None:
        cw_extras["score"] = card.score
    capability_id = f"{card.namespace}:{card.name}" if card.namespace else card.id
    option: Any = ws.SelectableItem(
        id=card.id,
        label=card.name,
        description=card.description,
        capability_id=capability_id,
        metadata={_CW_META_KEY: cw_extras},
    )
    return option


def _spec_option_to_cw_card(spec_option: _ws_types.SelectableItem) -> ChoiceCard:
    """Internal: convert a spec menu option back into a contextweaver ChoiceCard."""
    raw_meta = getattr(spec_option, "metadata", None) or {}
    cw_extras = dict(raw_meta).get(_CW_META_KEY) or {}
    score = cw_extras.get("score")
    return ChoiceCard(
        id=spec_option.id,
        name=spec_option.label,
        description=spec_option.description,
        tags=list(cw_extras.get("tags", [])),
        kind=cw_extras.get("kind", "tool"),
        namespace=cw_extras.get("namespace", ""),
        has_schema=bool(cw_extras.get("has_schema", False)),
        score=float(score) if score is not None else None,
        cost_hint=float(cw_extras.get("cost_hint", 0.0)),
        side_effects=bool(cw_extras.get("side_effects", False)),
    )


def to_weaver_choice_card(
    card: ChoiceCard,
    *,
    menu_id: str | None = None,
    context_hint: str | None = None,
) -> _ws_types.ChoiceCard:
    """Wrap a single contextweaver :class:`ChoiceCard` as a one-item spec menu.

    The spec's ``ChoiceCard`` is 1:N — contextweaver's is 1:1.  This helper
    creates a spec menu containing exactly one option built from *card*.

    Args:
        card: The contextweaver card to wrap.
        menu_id: Optional explicit ID for the spec menu.  Defaults to
            ``f"menu:{card.id}"``.
        context_hint: Optional spec ``context_hint`` for LLM guidance.
    """
    ws = _import_weaver_contracts()
    spec_menu: Any = ws.ChoiceCard(
        id=menu_id if menu_id is not None else f"menu:{card.id}",
        items=[_cw_card_to_spec_option(card)],
        context_hint=context_hint,
        metadata={},
    )
    return spec_menu


def to_weaver_choice_cards(
    cards: list[ChoiceCard],
    *,
    menu_id: str,
    context_hint: str | None = None,
) -> _ws_types.ChoiceCard:
    """Group a list of contextweaver :class:`ChoiceCard` into one spec menu.

    Args:
        cards: One or more contextweaver cards.  Must be non-empty (the spec's
            ``ChoiceCard.items`` requires ``minItems=1``).
        menu_id: Required ID for the spec menu.
        context_hint: Optional spec ``context_hint`` for LLM guidance.

    Raises:
        CatalogError: When *cards* is empty.
    """
    if not cards:
        raise CatalogError(
            "weaver_contracts.ChoiceCard requires at least one item; got an empty list"
        )
    ws = _import_weaver_contracts()
    spec_menu: Any = ws.ChoiceCard(
        id=menu_id,
        items=[_cw_card_to_spec_option(c) for c in cards],
        context_hint=context_hint,
        metadata={},
    )
    return spec_menu


def from_weaver_choice_card(spec_card: _ws_types.ChoiceCard) -> list[ChoiceCard]:
    """Convert a spec menu to a list of contextweaver :class:`ChoiceCard`.

    Each spec ``SelectableItem`` becomes one contextweaver card.
    """
    return [_spec_option_to_cw_card(item) for item in spec_card.items]


def from_weaver_choice_card_single(spec_card: _ws_types.ChoiceCard) -> ChoiceCard:
    """Convert a single-option spec menu into one contextweaver :class:`ChoiceCard`.

    Raises:
        CatalogError: When *spec_card* does not contain exactly one item.
            Use :func:`from_weaver_choice_card` for multi-option menus.
    """
    items = list(spec_card.items)
    if len(items) != 1:
        raise CatalogError(
            "from_weaver_choice_card_single expects a single-item spec menu; "
            f"got {len(items)} items"
        )
    return _spec_option_to_cw_card(items[0])


# ---------------------------------------------------------------------------
# RoutingDecision
# ---------------------------------------------------------------------------


def to_weaver_routing_decision(decision: RoutingDecision) -> _ws_types.RoutingDecision:
    """Convert a contextweaver :class:`RoutingDecision` to the spec form.

    The contextweaver ``choice_cards`` list (a flat list of 1:1 cards) is
    grouped into a single spec ``ChoiceCard`` menu whose ``items`` carry the
    individual options.  The contextweaver-side ``selected_card_id`` (which
    refers to a card *within* the flat list) is remapped to the synthetic
    menu's ``id`` whenever the underlying item is present in the menu, so
    downstream consumers can resolve which menu was selected.  Round-trip is
    lossless via :func:`from_weaver_routing_decision`.

    Raises:
        CatalogError: When ``decision.choice_cards`` is empty (the spec
            requires at least one card).
    """
    if not decision.choice_cards:
        raise CatalogError("weaver_contracts.RoutingDecision requires at least one ChoiceCard")
    ws = _import_weaver_contracts()
    menu_id = f"{decision.id}:menu"
    spec_menu = to_weaver_choice_cards(decision.choice_cards, menu_id=menu_id)
    # ``decision.selected_card_id`` refers to a contextweaver 1:1 card by ID;
    # the spec's ``selected_card_id`` references one of the grouped spec menus.
    # Remap to the synthetic menu's ID when the selected card is present in
    # the flat list — otherwise leave it untouched (e.g. when the caller has
    # already supplied a spec-shaped menu ID).
    cw_card_ids = {card.id for card in decision.choice_cards}
    spec_selected_card_id = decision.selected_card_id
    if spec_selected_card_id is not None and spec_selected_card_id in cw_card_ids:
        spec_selected_card_id = menu_id
    spec_decision: Any = ws.RoutingDecision(
        id=decision.id,
        choice_cards=[spec_menu],
        timestamp=_ensure_aware(decision.timestamp),
        selected_item_id=decision.selected_item_id,
        selected_card_id=spec_selected_card_id,
        context_summary=decision.context_summary,
        metadata=dict(decision.metadata),
    )
    return spec_decision


def from_weaver_routing_decision(
    spec_decision: _ws_types.RoutingDecision,
) -> RoutingDecision:
    """Convert a spec ``RoutingDecision`` back to the contextweaver form.

    Flattens options from every spec menu in ``spec_decision.choice_cards``
    into a single list of contextweaver :class:`ChoiceCard` instances.  When
    ``selected_card_id`` references one of those synthetic menus (the
    ``f"{decision.id}:menu"`` produced by :func:`to_weaver_routing_decision`),
    it is remapped back to the contextweaver card that contains the selected
    item so the round-trip is lossless.
    """
    cw_cards: list[ChoiceCard] = []
    menu_ids_seen: set[str] = set()
    for spec_menu in spec_decision.choice_cards:
        menu_ids_seen.add(spec_menu.id)
        cw_cards.extend(from_weaver_choice_card(spec_menu))
    selected_card_id = spec_decision.selected_card_id
    if (
        selected_card_id is not None
        and selected_card_id in menu_ids_seen
        and spec_decision.selected_item_id is not None
    ):
        # Reverse the to_weaver_routing_decision remap: prefer the CW card
        # whose ID matches the selected item.
        for card in cw_cards:
            if card.id == spec_decision.selected_item_id:
                selected_card_id = card.id
                break
    return RoutingDecision(
        id=spec_decision.id,
        choice_cards=cw_cards,
        timestamp=_ensure_aware(spec_decision.timestamp),
        selected_item_id=spec_decision.selected_item_id,
        selected_card_id=selected_card_id,
        context_summary=spec_decision.context_summary,
        metadata=dict(getattr(spec_decision, "metadata", None) or {}),
    )


# ---------------------------------------------------------------------------
# Frame ↔ ResultEnvelope
# ---------------------------------------------------------------------------


def to_weaver_frame(
    envelope: ResultEnvelope,
    *,
    frame_id: str,
    capability_id: str,
    created_at: datetime | None = None,
) -> _ws_types.Frame:
    """Convert a contextweaver :class:`ResultEnvelope` to a spec ``Frame``.

    The spec's ``Frame`` requires ``frame_id``, ``capability_id``, and
    ``created_at`` — none of which are present in :class:`ResultEnvelope` —
    so they must be supplied by the caller (typically derived from the
    surrounding tool-call provenance).

    ``ResultEnvelope`` fields that have no direct spec preimage (``status``,
    ``facts``, ``views``, ``artifacts`` metadata, ``provenance``) are stored
    under ``metadata["_contextweaver"]`` and ``structured_data`` so the round
    trip via :func:`from_weaver_frame` is lossless.

    Args:
        envelope: The contextweaver envelope to convert.
        frame_id: Unique frame identifier; must be non-empty.
        capability_id: ID of the capability that produced the result; must be
            non-empty.
        created_at: Optional timezone-aware timestamp.  Defaults to
            ``datetime.now(timezone.utc)``.

    Raises:
        CatalogError: When ``weaver_contracts`` is not installed.
    """
    ws = _import_weaver_contracts()
    when = _ensure_aware(created_at) if created_at is not None else datetime.now(timezone.utc)
    summary = envelope.summary if envelope.summary else "(no summary)"
    structured_data: dict[str, Any] = {
        "status": envelope.status,
        "facts": list(envelope.facts),
        "views": [v.to_dict() for v in envelope.views],
    }
    handle_refs = [a.handle for a in envelope.artifacts]
    cw_extras: dict[str, Any] = {
        "artifacts": [a.to_dict() for a in envelope.artifacts],
        "provenance": dict(envelope.provenance),
        "original_summary": envelope.summary,
    }
    metadata = {_CW_META_KEY: cw_extras}
    redaction_raw = envelope.provenance.get("redaction_notes")
    redaction_notes = redaction_raw if isinstance(redaction_raw, str) and redaction_raw else None
    spec_frame: Any = ws.Frame(
        frame_id=frame_id,
        capability_id=capability_id,
        summary=summary,
        created_at=when,
        structured_data=structured_data,
        handle_refs=handle_refs,
        redaction_notes=redaction_notes,
        metadata=metadata,
    )
    return spec_frame


def from_weaver_frame(spec_frame: _ws_types.Frame) -> ResultEnvelope:
    """Convert a spec ``Frame`` back to a contextweaver :class:`ResultEnvelope`.

    Frames produced by :func:`to_weaver_frame` round-trip losslessly via
    ``metadata["_contextweaver"]``.  Frames produced elsewhere lose status /
    fact / view information unless they happen to use the same encoding;
    the adapter falls back to ``status="ok"``, empty facts/views, and
    constructs minimal :class:`ArtifactRef` stubs from ``handle_refs``.
    """
    raw_meta = getattr(spec_frame, "metadata", None) or {}
    metadata = dict(raw_meta)
    cw_origin = _CW_META_KEY in metadata
    cw_extras = metadata.pop(_CW_META_KEY, None) or {}
    cw_artifacts_data = cw_extras.get("artifacts", [])
    artifacts: list[ArtifactRef]
    if cw_artifacts_data:
        artifacts = [ArtifactRef.from_dict(a) for a in cw_artifacts_data]
    else:
        artifacts = [
            ArtifactRef(handle=h, media_type="application/octet-stream", size_bytes=0)
            for h in (spec_frame.handle_refs or [])
        ]
    structured = spec_frame.structured_data or {}
    facts = list(structured.get("facts", []))
    views_data = structured.get("views", [])
    views = [ViewSpec.from_dict(v) for v in views_data]
    status_raw = structured.get("status", "ok")
    status: Any = status_raw if status_raw in ("ok", "partial", "error") else "ok"
    provenance = dict(cw_extras.get("provenance", {}))
    if spec_frame.redaction_notes:
        provenance.setdefault("redaction_notes", spec_frame.redaction_notes)
    # Only reverse the ``"(no summary)"`` sentinel when ``_contextweaver``
    # metadata proves the Frame was produced by ``to_weaver_frame``.  Foreign
    # producers might legitimately use that exact string and we must not lose
    # their data.
    original_summary = cw_extras.get("original_summary")
    if isinstance(original_summary, str):
        summary = original_summary
    elif cw_origin and spec_frame.summary == "(no summary)":
        summary = ""
    else:
        summary = spec_frame.summary
    return ResultEnvelope(
        status=status,
        summary=summary,
        facts=facts,
        artifacts=artifacts,
        views=views,
        provenance=provenance,
    )


__all__ = [
    "from_weaver_choice_card",
    "from_weaver_choice_card_single",
    "from_weaver_frame",
    "from_weaver_routing_decision",
    "from_weaver_selectable_item",
    "to_weaver_choice_card",
    "to_weaver_choice_cards",
    "to_weaver_frame",
    "to_weaver_routing_decision",
    "to_weaver_selectable_item",
]

"""Structured tool-selection contract for the routing engine.

Covers the two halves of the route → select turn, the single point where a
downstream model's output re-enters the deterministic pipeline:

* :func:`selection_schema` (issue #515) — emit a provider-native
  constrained-selection schema (the routed candidate IDs as a JSON-Schema
  ``enum``) so callers can force the model to pick *only* among routed
  candidates at generation time.  "Constrain before."
* :func:`validate_selection` (issue #479) — validate, and deterministically
  repair, a model's selected ID against the routed candidate set, returning a
  typed :class:`SelectionValidation` outcome.  "Validate after."

Both functions are pure and deterministic; neither calls a model.  They are
the shared, tested behaviour the framework adapters and the MCP gateway use so
every integrator gets the same accept/repair/reject semantics instead of
re-implementing ad-hoc parsing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Literal

from contextweaver.exceptions import ConfigError, RouteError

#: Providers :func:`selection_schema` can shape its output for.  ``json_schema``
#: is the provider-neutral form; the others wrap it in the request envelope the
#: named provider's constrained-output API expects.
SELECTION_SCHEMA_PROVIDERS: tuple[str, ...] = ("json_schema", "openai", "anthropic")

#: Outcome of validating a model's selected ID against the routed candidates.
SelectionStatus = Literal["accepted", "repaired", "rejected"]


def selection_schema(
    candidate_ids: list[str],
    *,
    provider: str = "json_schema",
    property_name: str = "tool_id",
    schema_name: str = "tool_selection",
) -> dict[str, Any]:
    """Render *candidate_ids* as a constrained-selection schema (issue #515).

    The core is a minimal JSON Schema whose single property is constrained to
    an ``enum`` of the routed candidate IDs, so a model using the schema for
    constrained generation cannot invent or misspell a tool ID — the most
    common routing failure — at the source.  This complements the post-hoc
    :func:`validate_selection` (issue #479): constrain before, validate after.

    Args:
        candidate_ids: The routed candidate IDs, typically
            ``RouteResult.candidate_ids``.  Order is preserved; duplicates are
            removed (keeping first occurrence) so the ``enum`` is well-formed.
        provider: One of :data:`SELECTION_SCHEMA_PROVIDERS`.  ``"json_schema"``
            returns the bare schema; ``"openai"`` wraps it in the
            ``response_format`` ``json_schema`` envelope; ``"anthropic"`` wraps
            it as a tool definition with the schema as ``input_schema``.
        property_name: Name of the selection property (default ``"tool_id"``).
        schema_name: Name stamped on the provider-wrapped variants.

    Returns:
        A JSON-compatible dict ready to pass to the provider's structured-output
        / tool-choice API.

    Raises:
        RouteError: If *candidate_ids* is empty — there is nothing to choose.
        ConfigError: If *provider* is not a recognised value.
    """
    if provider not in SELECTION_SCHEMA_PROVIDERS:
        raise ConfigError(
            f"Unknown selection-schema provider {provider!r}; "
            f"valid options: {list(SELECTION_SCHEMA_PROVIDERS)}"
        )
    if not candidate_ids:
        raise RouteError("selection_schema requires at least one candidate id")
    # De-duplicate while preserving order so the enum mirrors the ranked
    # shortlist and round-trips deterministically.
    enum = list(dict.fromkeys(candidate_ids))
    base: dict[str, Any] = {
        "type": "object",
        "properties": {property_name: {"type": "string", "enum": enum}},
        "required": [property_name],
        "additionalProperties": False,
    }
    if provider == "openai":
        return {
            "type": "json_schema",
            "json_schema": {"name": schema_name, "strict": True, "schema": base},
        }
    if provider == "anthropic":
        return {
            "name": schema_name,
            "description": "Select exactly one tool_id from the routed candidates.",
            "input_schema": base,
        }
    return base


@dataclass
class SelectionValidation:
    """Typed outcome of validating a model's selected ID (issue #479).

    Attributes:
        status: ``"accepted"`` (exact match), ``"repaired"`` (matched after a
            deterministic normalisation), or ``"rejected"`` (no safe match).
        selected_id: The canonical candidate ID the selection resolved to, or
            ``None`` when ``status == "rejected"``.  Always one of the offered
            candidates when non-``None``.
        raw_id: The selection exactly as supplied by the caller.
        repair: When ``status == "repaired"``, the rule that matched —
            ``"strip"``, ``"case_fold"``, or ``"prefix"``.  ``None`` otherwise.
        reason: When ``status == "rejected"``, a stable machine-readable reason
            (e.g. ``"not_a_candidate"``, ``"empty_selection"``,
            ``"ambiguous_case_fold"``, ``"ambiguous_prefix"``).  ``None``
            otherwise.
    """

    status: SelectionStatus
    selected_id: str | None
    raw_id: str
    repair: str | None = None
    reason: str | None = None

    @property
    def ok(self) -> bool:
        """``True`` when the selection resolved to a candidate (accepted/repaired)."""
        return self.status != "rejected"

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "status": self.status,
            "selected_id": self.selected_id,
            "raw_id": self.raw_id,
            "repair": self.repair,
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SelectionValidation:
        """Deserialise from a JSON-compatible dict."""
        return cls(
            status=data["status"],
            selected_id=data.get("selected_id"),
            raw_id=str(data.get("raw_id", "")),
            repair=data.get("repair"),
            reason=data.get("reason"),
        )


def validate_selection(
    selected_id: str | None,
    candidate_ids: list[str],
    *,
    repair: bool = True,
) -> SelectionValidation:
    """Validate *selected_id* against the routed *candidate_ids* (issue #479).

    This is the guard for the selection turn: it catches off-list selections
    before ``tool_execute`` and deterministically repairs near-miss selections
    (surrounding whitespace, case differences, unambiguous truncated IDs) so
    downstream automation can branch on a typed outcome.

    Repair (when *repair* is ``True``) is tried in a fixed, deterministic
    order against the whitespace-stripped selection; the first rule that
    yields a *unique* candidate wins:

    1. **exact** — the stripped value is a candidate (``"strip"`` when stripping
       was required, otherwise ``"accepted"``);
    2. **case_fold** — exactly one candidate matches case-insensitively;
    3. **prefix** — exactly one candidate starts with the stripped value
       (case-insensitively).

    Ambiguous case-fold / prefix matches (more than one candidate) are
    **rejected**, never guessed, so the contract can never silently route to
    the wrong tool.

    Args:
        selected_id: The model's selected ID, or ``None``.
        candidate_ids: The offered candidates, typically
            ``RouteResult.candidate_ids``.
        repair: When ``False``, only an exact match is accepted; every other
            input is rejected with no normalisation.

    Returns:
        A :class:`SelectionValidation` describing the outcome.
    """
    raw = selected_id if selected_id is not None else ""
    candidates = list(candidate_ids)
    candidate_set = set(candidates)

    norm = raw.strip()
    if not norm:
        return SelectionValidation(
            status="rejected", selected_id=None, raw_id=raw, reason="empty_selection"
        )

    if norm in candidate_set:
        if norm == raw:
            return SelectionValidation(status="accepted", selected_id=norm, raw_id=raw)
        return SelectionValidation(status="repaired", selected_id=norm, raw_id=raw, repair="strip")

    if not repair:
        return SelectionValidation(
            status="rejected", selected_id=None, raw_id=raw, reason="not_a_candidate"
        )

    lowered = norm.lower()
    case_matches = [c for c in candidates if c.lower() == lowered]
    if len(case_matches) == 1:
        return SelectionValidation(
            status="repaired", selected_id=case_matches[0], raw_id=raw, repair="case_fold"
        )
    if len(case_matches) > 1:
        return SelectionValidation(
            status="rejected", selected_id=None, raw_id=raw, reason="ambiguous_case_fold"
        )

    prefix_matches = [c for c in candidates if c.lower().startswith(lowered)]
    if len(prefix_matches) == 1:
        return SelectionValidation(
            status="repaired", selected_id=prefix_matches[0], raw_id=raw, repair="prefix"
        )
    if len(prefix_matches) > 1:
        return SelectionValidation(
            status="rejected", selected_id=None, raw_id=raw, reason="ambiguous_prefix"
        )

    return SelectionValidation(
        status="rejected", selected_id=None, raw_id=raw, reason="not_a_candidate"
    )


__all__ = [
    "SELECTION_SCHEMA_PROVIDERS",
    "SelectionStatus",
    "SelectionValidation",
    "selection_schema",
    "validate_selection",
]

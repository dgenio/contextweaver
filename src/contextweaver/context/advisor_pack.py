"""AdvisorPack — bounded, planning-only model escalation (issue #741).

A cheap/fast agent occasionally faces a decision worth a second opinion from
a stronger model: which of N candidate plans to pursue, whether a risky step
is warranted, how to sequence a migration.  ``AdvisorPack`` packages that
escalation as **advice-only**: the advisor model receives a compact,
budget-bounded prompt and returns an opinion; it never selects tools, never
executes anything, and never bypasses policy — the calling agent (and its
policy layer) remains the sole decision-maker, per the deterministic-first
rubric (``docs/agent-context/model-backed-features.md``, rows 1–3).

The model call goes through the caller-supplied ``call_fn`` (no LLM SDK
dependency), optionally wrapped in :class:`~contextweaver.extras.llm_guard.GuardedCallFn`
when a :class:`~contextweaver.extras.llm_guard.GuardPolicy` is provided.
Guard rejections (call cap, open circuit) propagate to the caller — the
caller chose to escalate and must decide what "no advice available" means.
Malformed advisor output degrades to a raw-text advice payload instead of
raising.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from contextweaver.exceptions import ConfigError
from contextweaver.tokens import count

if TYPE_CHECKING:
    from contextweaver.extras.llm_guard import GuardPolicy

#: Confidence levels an advisor may self-report.
CONFIDENCE_LEVELS: tuple[str, ...] = ("low", "medium", "high")

#: Deterministic marker appended when the advisor named an unknown option.
_UNKNOWN_OPTION_MARKER = "[advisor: option not in candidate set]"


@dataclass
class AdvisorRequest:
    """One planning question escalated to a stronger advice-only model.

    Attributes:
        question: The decision the caller faces.
        options: Candidate answers/plans the caller is choosing between.
        context_summary: Compact task context (truncated to ``budget_tokens``).
        constraints: Hard constraints the advisor must respect in its advice.
        budget_tokens: Token budget for ``context_summary`` in the prompt.
    """

    question: str
    options: list[str] = field(default_factory=list)
    context_summary: str = ""
    constraints: list[str] = field(default_factory=list)
    budget_tokens: int = 800

    def __post_init__(self) -> None:
        if not self.question.strip():
            raise ConfigError("AdvisorRequest.question must be non-empty")
        if self.budget_tokens < 1:
            raise ConfigError("AdvisorRequest.budget_tokens must be >= 1")

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "question": self.question,
            "options": list(self.options),
            "context_summary": self.context_summary,
            "constraints": list(self.constraints),
            "budget_tokens": self.budget_tokens,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AdvisorRequest:
        """Deserialise from a JSON-compatible dict."""
        return cls(
            question=str(data["question"]),
            options=[str(o) for o in data.get("options", [])],
            context_summary=str(data.get("context_summary", "")),
            constraints=[str(c) for c in data.get("constraints", [])],
            budget_tokens=int(data.get("budget_tokens", 800)),
        )


@dataclass
class AdvisorResponse:
    """Advice returned by the escalation model — never a decision.

    Attributes:
        advice: The advisor's reasoning/opinion, free text.
        preferred_option: One of the request's ``options``, or ``None`` when
            the advisor abstained, answered off-list, or returned malformed
            output.
        confidence: Advisor-reported confidence, or ``None`` when absent.
        raw: The unparsed model completion, retained for audit.
        provider_metadata: Caller-supplied provider/model/version identifiers.
    """

    advice: str
    preferred_option: str | None = None
    confidence: Literal["low", "medium", "high"] | None = None
    raw: str = ""
    provider_metadata: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "advice": self.advice,
            "preferred_option": self.preferred_option,
            "confidence": self.confidence,
            "raw": self.raw,
            "provider_metadata": dict(self.provider_metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> AdvisorResponse:
        """Deserialise from a JSON-compatible dict."""
        confidence = data.get("confidence")
        if confidence is not None and confidence not in CONFIDENCE_LEVELS:
            raise ConfigError(f"AdvisorResponse.confidence must be one of {CONFIDENCE_LEVELS}")
        return cls(
            advice=str(data.get("advice", "")),
            preferred_option=(
                str(data["preferred_option"]) if data.get("preferred_option") is not None else None
            ),
            confidence=confidence,
            raw=str(data.get("raw", "")),
            provider_metadata={
                str(k): str(v) for k, v in data.get("provider_metadata", {}).items()
            },
        )


def _truncate_to_budget(text: str, budget_tokens: int) -> str:
    """Trim *text* so its token count fits *budget_tokens* (deterministic)."""
    if count(text) <= budget_tokens:
        return text
    lo, hi = 0, len(text)
    while lo < hi:
        mid = (lo + hi + 1) // 2
        if count(text[:mid]) <= budget_tokens:
            lo = mid
        else:
            hi = mid - 1
    return text[:lo]


def build_advisor_prompt(request: AdvisorRequest) -> str:
    """Render the deterministic, budget-bounded advisor prompt.

    The prompt instructs strict-JSON, advice-only output.  The context
    summary is truncated to :attr:`AdvisorRequest.budget_tokens` via the
    built-in token counter; every other section is included verbatim.
    """
    lines = [
        "You are an advisor. Give planning advice only — you cannot execute",
        "anything and your preference is not a decision.",
        "",
        f"Question: {request.question}",
    ]
    if request.options:
        lines.append("Options:")
        lines.extend(f"  {i + 1}. {option}" for i, option in enumerate(request.options))
    if request.constraints:
        lines.append("Constraints:")
        lines.extend(f"  - {constraint}" for constraint in request.constraints)
    if request.context_summary:
        lines.append("Context:")
        lines.append(_truncate_to_budget(request.context_summary, request.budget_tokens))
    lines.extend(
        [
            "",
            "Answer with strict JSON, no prose around it:",
            '{"advice": "<your reasoning>", "preferred_option": "<exact option text or null>",',
            ' "confidence": "low|medium|high"}',
        ]
    )
    return "\n".join(lines)


def _parse_advice(raw: str, options: list[str]) -> AdvisorResponse:
    """Parse the advisor completion, degrading instead of raising."""
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return AdvisorResponse(advice=raw.strip(), raw=raw)
    if not isinstance(data, dict):
        return AdvisorResponse(advice=raw.strip(), raw=raw)
    advice = str(data.get("advice", "")).strip() or raw.strip()
    preferred = data.get("preferred_option")
    preferred_option: str | None = None
    if isinstance(preferred, str) and preferred:
        if preferred in options:
            preferred_option = preferred
        else:
            advice = f"{advice}\n{_UNKNOWN_OPTION_MARKER}"
    confidence_raw = data.get("confidence")
    confidence = confidence_raw if confidence_raw in CONFIDENCE_LEVELS else None
    return AdvisorResponse(
        advice=advice, preferred_option=preferred_option, confidence=confidence, raw=raw
    )


def ask_advisor(
    request: AdvisorRequest,
    call_fn: Callable[[str], str],
    *,
    guard_policy: GuardPolicy | None = None,
    provider_metadata: dict[str, str] | None = None,
) -> AdvisorResponse:
    """Escalate *request* to the advice-only model behind *call_fn*.

    Args:
        request: The planning question, options, and context.
        call_fn: User-supplied model callable (prompt → completion text).
        guard_policy: Optional :class:`~contextweaver.extras.llm_guard.GuardPolicy`;
            when given, the call is dispatched through
            :class:`~contextweaver.extras.llm_guard.GuardedCallFn` and guard
            rejections (:class:`~contextweaver.exceptions.PolicyViolationError`)
            propagate — the caller opted into escalation and owns the fallback.
        provider_metadata: Provider/model/version identifiers recorded on the
            response for auditability (rubric row 6).

    Returns:
        An :class:`AdvisorResponse`.  Malformed model output degrades to a
        raw-text advice payload with ``preferred_option=None``.
    """
    prompt = build_advisor_prompt(request)
    if guard_policy is not None:
        from contextweaver.extras.llm_guard import GuardedCallFn

        raw = GuardedCallFn(call_fn, guard_policy)(prompt)
    else:
        raw = call_fn(prompt)
    response = _parse_advice(raw, request.options)
    response.provider_metadata = dict(provider_metadata or {})
    return response


__all__ = [
    "CONFIDENCE_LEVELS",
    "AdvisorRequest",
    "AdvisorResponse",
    "ask_advisor",
    "build_advisor_prompt",
]

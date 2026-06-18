"""Optional model-assisted canonicalizer for consolidation (issue #682).

The deterministic consolidation core (:mod:`contextweaver.context.consolidation`)
picks a representative ``canonical_text`` for each cluster without any model.
This private helper lets callers refine that text with a user-supplied
``call_fn`` (the established zero-dependency plugin pattern from
:mod:`contextweaver.extras.llm_summarizer`) under strict guardrails:

* **Opt-in.** Only runs when a ``call_fn`` is supplied and the run is not in
  ``deterministic`` mode.
* **Fail-closed.** Any exception, a non-string / blank completion, a result
  that introduces content tokens absent from the source cluster, or one that
  introduces a *negation* absent from the source falls back to the deterministic
  ``canonical_text``. A model may only ever *rephrase* grounded content — it can
  neither inject a new entity nor flip the polarity of a durable fact.

The content-token check reuses :func:`contextweaver._utils.tokenize` so grounding
matches the rest of the library's text normalisation. Because ``tokenize`` drops
stop-words, it would not catch an injected negation (``"is safe"`` →
``"is not safe"`` share the same content tokens); the separate negation check in
:func:`_negations` closes that gap.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable

from contextweaver._utils import tokenize

logger = logging.getLogger("contextweaver.context")

#: Polarity / negation terms that :func:`~contextweaver._utils.tokenize` discards
#: as stop-words but which invert meaning. They must be grounded too: a merge may
#: not introduce a negation absent from the source notes.
_NEGATION_TERMS = frozenset({"not", "no", "never", "none", "cannot", "without", "neither", "nor"})


def _negations(text: str) -> set[str]:
    """Return the negation terms present in *text* (lower-cased, incl. ``n't``)."""
    words = set(re.findall(r"[a-z']+", text.lower()))
    found = {term for term in _NEGATION_TERMS if term in words}
    if any(word.endswith("n't") for word in words):
        found.add("n't")
    return found


#: Instruction prepended to the cluster's source summaries for the merge call.
DEFAULT_MERGE_PROMPT = (
    "Merge the following related memory notes into a single concise fact "
    "sentence. Use only information present in the notes; do not add details. "
    "Return only the sentence.\n\n"
)

#: Upper bound on characters sent to the model.
_MAX_INPUT = 4000


def refine_canonical_text(
    canonical_text: str,
    source_texts: list[str],
    call_fn: Callable[[str], str],
    *,
    system_prompt: str = DEFAULT_MERGE_PROMPT,
) -> tuple[str, bool]:
    """Return a model-refined canonical text, or fall back deterministically.

    Args:
        canonical_text: The deterministic representative text (the fallback).
        source_texts: The cluster's source episode summaries; used both as the
            model prompt body and as the grounding vocabulary.
        call_fn: User-supplied ``prompt -> completion`` callable. No LLM SDK is
            imported; bring your own model.
        system_prompt: Instruction prepended to the joined source texts.

    Returns:
        A ``(text, merged_by_llm)`` tuple. ``merged_by_llm`` is ``True`` only
        when the model produced a grounded, non-blank result that was accepted.
    """
    allowed = set()
    source_negations: set[str] = set()
    for text in source_texts:
        allowed |= tokenize(text)
        source_negations |= _negations(text)
    body = "\n".join(source_texts)[:_MAX_INPUT]
    try:
        result = call_fn(f"{system_prompt}{body}")
    except Exception as exc:  # noqa: BLE001 - any model failure must degrade safely
        logger.warning("consolidation merge: call_fn raised (%s); using deterministic text", exc)
        return canonical_text, False

    if not isinstance(result, str) or not result.strip():
        logger.warning("consolidation merge: blank/non-string completion; using deterministic text")
        return canonical_text, False
    merged = result.strip()
    new_tokens = tokenize(merged) - allowed
    if new_tokens:
        logger.warning(
            "consolidation merge: ungrounded tokens %s; using deterministic text",
            sorted(new_tokens),
        )
        return canonical_text, False
    introduced_negations = _negations(merged) - source_negations
    if introduced_negations:
        logger.warning(
            "consolidation merge: introduced negation(s) %s; using deterministic text",
            sorted(introduced_negations),
        )
        return canonical_text, False
    return merged, True


__all__ = ["DEFAULT_MERGE_PROMPT", "refine_canonical_text"]

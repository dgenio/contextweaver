"""Optional model-assisted canonicalizer for consolidation (issue #682).

The deterministic consolidation core (:mod:`contextweaver.context.consolidation`)
picks a representative ``canonical_text`` for each cluster without any model.
This private helper lets callers refine that text with a user-supplied
``call_fn`` (the established zero-dependency plugin pattern from
:mod:`contextweaver.extras.llm_summarizer`) under strict guardrails:

* **Opt-in.** Only runs when a ``call_fn`` is supplied and the run is not in
  ``deterministic`` mode.
* **Fail-closed.** Any exception, a non-string / blank completion, or a result
  that introduces tokens absent from the source cluster falls back to the
  deterministic ``canonical_text``. A model can only ever *rephrase* grounded
  content — it can never inject a new entity into a durable fact.

The "no new tokens" check reuses :func:`contextweaver._utils.tokenize` so the
grounding test matches the rest of the library's text normalisation.
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from contextweaver._utils import tokenize

logger = logging.getLogger("contextweaver.context")

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
    for text in source_texts:
        allowed |= tokenize(text)
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
    return merged, True


__all__ = ["DEFAULT_MERGE_PROMPT", "refine_canonical_text"]

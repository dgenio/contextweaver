"""LLM-backed ``Summarizer`` / ``Extractor`` plugins for contextweaver (issue #26).

The context firewall (:mod:`contextweaver.context.firewall`) already accepts
optional :class:`~contextweaver.protocols.Summarizer` and
:class:`~contextweaver.protocols.Extractor` implementations and falls back to
the deterministic rule-based path when none is supplied (Phase 1 of #26).  This
module ships the optional **LLM-backed** implementations (Phase 2): drop-in
plugins for agent loops that already have a model handy and want higher-quality
summaries / fact extraction for *messy* outputs (HTML scrapes, log dumps,
unstructured API responses) that the rule-based heuristics summarise poorly.

Zero new dependencies
----------------------

Neither class imports an LLM SDK.  The caller supplies a
``call_fn: Callable[[str], str]`` — a function that takes a prompt and returns
the model's text completion.  This keeps the core install free of any vendor
SDK while letting users wire whichever model they already call.

Fail-safe by construction
--------------------------

Both classes degrade to the rule-based path on *any* failure of ``call_fn``
(timeout, transport error, empty/blank completion).  The firewall additionally
wraps summariser/extractor calls in its own ``try/except``; this module's
fallback means a flaky model never even surfaces as a degraded
``ResultEnvelope`` status when a deterministic answer is available.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from typing import Any

from contextweaver.protocols import Extractor, Summarizer
from contextweaver.summarize.extract import StructuredExtractor
from contextweaver.summarize.rules import RuleBasedSummarizer

logger = logging.getLogger("contextweaver.extras")

DEFAULT_SUMMARY_PROMPT = (
    "Summarise the following tool output concisely for an AI agent. "
    "Capture the key result, any errors, and salient numbers in at most "
    "three sentences. Do not invent information that is not present.\n\n"
)
DEFAULT_EXTRACT_PROMPT = (
    "Extract the salient facts from the following tool output as a plain list, "
    "one fact per line, no numbering or commentary. Each fact must be grounded "
    "in the text. If there are no facts, return nothing.\n\n"
)

# Strips a leading bullet (``-``/``*``/``•``) or ordered-list marker
# (``1.``/``2)``) from an LLM-produced line so the fact text stands alone.
_LIST_PREFIX_RE = re.compile(r"^\s*(?:[-*•]\s+|\d+[.)]\s+)?(.*\S)\s*$")


def _parse_fact_lines(text: str) -> list[str]:
    """Parse a model completion into a deduplicated list of fact strings.

    Splits on newlines, strips any bullet / ordered-list prefix, and drops
    blank lines.  Order is preserved; duplicates are removed (first wins).
    """
    facts: list[str] = []
    seen: set[str] = set()
    for line in text.splitlines():
        match = _LIST_PREFIX_RE.match(line)
        if match is None:
            continue
        fact = match.group(1).strip()
        if fact and fact not in seen:
            seen.add(fact)
            facts.append(fact)
    return facts


class LlmSummarizer:
    """:class:`~contextweaver.protocols.Summarizer` backed by a user-supplied model.

    Args:
        call_fn: Callable taking a prompt string and returning the model's
            text completion.  Bring your own — no LLM SDK is imported.
        max_input: Upper bound on raw characters sent to the model; longer
            inputs are truncated.  Defaults to ``4000``.
        system_prompt: Instruction prepended to the raw output.  Defaults to
            :data:`DEFAULT_SUMMARY_PROMPT`.
        fallback: :class:`~contextweaver.protocols.Summarizer` used when
            ``call_fn`` raises or returns a blank completion.  Defaults to a
            :class:`~contextweaver.summarize.rules.RuleBasedSummarizer`.
    """

    def __init__(
        self,
        call_fn: Callable[[str], str],
        *,
        max_input: int = 4000,
        system_prompt: str = DEFAULT_SUMMARY_PROMPT,
        fallback: Summarizer | None = None,
    ) -> None:
        self._call = call_fn
        self._max_input = max_input
        self._system_prompt = system_prompt
        self._fallback: Summarizer = fallback if fallback is not None else RuleBasedSummarizer()

    def summarize(self, raw: str, metadata: dict[str, Any] | None = None) -> str:
        """Return an LLM summary of *raw*, or the rule-based fallback on failure."""
        meta = metadata or {}
        try:
            result = self._call(f"{self._system_prompt}{raw[: self._max_input]}")
            if not isinstance(result, str) or not result.strip():
                raise ValueError("LLM summariser returned an empty completion")
            return result.strip()
        except Exception as exc:  # noqa: BLE001 - any model failure must degrade safely
            logger.warning("LlmSummarizer: call_fn failed (%s); using rule-based fallback", exc)
            return self._fallback.summarize(raw, meta)


class LlmExtractor:
    """:class:`~contextweaver.protocols.Extractor` backed by a user-supplied model.

    Args:
        call_fn: Callable taking a prompt string and returning the model's
            text completion.  Bring your own — no LLM SDK is imported.
        max_input: Upper bound on raw characters sent to the model; longer
            inputs are truncated.  Defaults to ``4000``.
        system_prompt: Instruction prepended to the raw output.  Defaults to
            :data:`DEFAULT_EXTRACT_PROMPT`.
        fallback: :class:`~contextweaver.protocols.Extractor` used when
            ``call_fn`` raises or yields no facts.  Defaults to a
            :class:`~contextweaver.summarize.extract.StructuredExtractor`.
    """

    def __init__(
        self,
        call_fn: Callable[[str], str],
        *,
        max_input: int = 4000,
        system_prompt: str = DEFAULT_EXTRACT_PROMPT,
        fallback: Extractor | None = None,
    ) -> None:
        self._call = call_fn
        self._max_input = max_input
        self._system_prompt = system_prompt
        self._fallback: Extractor = fallback if fallback is not None else StructuredExtractor()

    def extract(self, raw: str, metadata: dict[str, Any] | None = None) -> list[str]:
        """Return facts extracted by the LLM, or the rule-based fallback on failure."""
        meta = metadata or {}
        try:
            result = self._call(f"{self._system_prompt}{raw[: self._max_input]}")
            if not isinstance(result, str):
                raise TypeError("LLM extractor returned a non-string completion")
            facts = _parse_fact_lines(result)
            if not facts:
                raise ValueError("LLM extractor produced no facts")
            return facts
        except Exception as exc:  # noqa: BLE001 - any model failure must degrade safely
            logger.warning("LlmExtractor: call_fn failed (%s); using rule-based fallback", exc)
            return self._fallback.extract(raw, meta)

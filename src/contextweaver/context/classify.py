"""Deterministic, opt-in sensitivity classification at ingestion (issue #542).

The sensitivity stage (:func:`~contextweaver.context.sensitivity.apply_sensitivity_filter`)
is only as good as the labels reaching it: every unlabelled
:class:`~contextweaver.types.ContextItem` defaults to
:attr:`~contextweaver.types.Sensitivity.public`, so enforcement never sees
content the caller forgot to classify.  Tool results are the riskiest class —
they routinely carry tokens, connection strings, and PII the calling code never
inspects.

This module ships a built-in :class:`HeuristicSensitivityClassifier` (the
:class:`~contextweaver.protocols.SensitivityClassifier` protocol lives in
:mod:`contextweaver.protocols`).  It is **opt-in**: a
:class:`~contextweaver.context.manager.ContextManager` only applies a classifier
when one is supplied, and a classifier can only ever *raise* a label, never lower
it.  Detection is deterministic and pattern-based, reusing the secret shapes in
:mod:`contextweaver.secrets` plus a small set of PII markers — no model, no
randomness — so enforcement stays auditable.
"""

from __future__ import annotations

import re

from contextweaver.context.sensitivity import _SENSITIVITY_ORDER
from contextweaver.secrets import contains_secret
from contextweaver.types import ContextItem, Sensitivity

# PII-shaped markers.  Conservative on purpose — these raise an item to
# ``confidential`` (one level below the ``restricted`` reserved for
# credential-shaped content).
_EMAIL_RE = re.compile(r"\b[\w.+\-]+@[\w\-]+\.[\w.\-]+\b")
_SSN_RE = re.compile(r"\b\d{3}-\d{2}-\d{4}\b")
# 13–16 digit sequences (optionally space/hyphen grouped) — credit-card shaped.
_CARD_RE = re.compile(r"\b(?:\d[ -]?){13,16}\b")

_PII_PATTERNS: tuple[re.Pattern[str], ...] = (_EMAIL_RE, _SSN_RE, _CARD_RE)


def _max_sensitivity(a: Sensitivity, b: Sensitivity) -> Sensitivity:
    """Return the higher of two sensitivity levels by the canonical order."""
    return a if _SENSITIVITY_ORDER[a] >= _SENSITIVITY_ORDER[b] else b


def detect_sensitivity(text: str) -> Sensitivity:
    """Classify *text* into a sensitivity level using deterministic heuristics.

    Returns:
        :attr:`~contextweaver.types.Sensitivity.restricted` when *text* carries
        credential-shaped content (see :func:`contextweaver.secrets.contains_secret`),
        :attr:`~contextweaver.types.Sensitivity.confidential` when it carries
        PII-shaped markers (email, SSN, or credit-card-shaped digits), and
        :attr:`~contextweaver.types.Sensitivity.public` otherwise.
    """
    if not text:
        return Sensitivity.public
    if contains_secret(text):
        return Sensitivity.restricted
    if any(pattern.search(text) for pattern in _PII_PATTERNS):
        return Sensitivity.confidential
    return Sensitivity.public


class HeuristicSensitivityClassifier:
    """Built-in deterministic :class:`~contextweaver.protocols.SensitivityClassifier`.

    Inspects :attr:`ContextItem.text` and returns the higher of the item's
    current label and the heuristic detection, so it can only raise a label.
    """

    def classify(self, item: ContextItem) -> Sensitivity:
        """Return the sensitivity *item* should carry (never below its current label)."""
        return _max_sensitivity(item.sensitivity, detect_sensitivity(item.text))


__all__ = [
    "HeuristicSensitivityClassifier",
    "detect_sensitivity",
]

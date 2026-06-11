"""Tests for contextweaver.context.classify (issue #542)."""

from __future__ import annotations

from contextweaver.context.classify import HeuristicSensitivityClassifier, detect_sensitivity
from contextweaver.protocols import SensitivityClassifier
from contextweaver.types import ContextItem, ItemKind, Sensitivity

AWS_KEY = "AKIAIOSFODNN7EXAMPLE"


def _item(text: str, sensitivity: Sensitivity = Sensitivity.public) -> ContextItem:
    return ContextItem(id="i1", kind=ItemKind.tool_result, text=text, sensitivity=sensitivity)


def test_detect_secret_is_restricted() -> None:
    assert detect_sensitivity(f"key={AWS_KEY}") is Sensitivity.restricted


def test_detect_pii_is_confidential() -> None:
    assert detect_sensitivity("contact alice@example.com") is Sensitivity.confidential
    assert detect_sensitivity("SSN 123-45-6789") is Sensitivity.confidential


def test_detect_clean_is_public() -> None:
    assert detect_sensitivity("the job finished in 3 seconds") is Sensitivity.public
    assert detect_sensitivity("") is Sensitivity.public


def test_classifier_satisfies_protocol() -> None:
    assert isinstance(HeuristicSensitivityClassifier(), SensitivityClassifier)


def test_classifier_raises_label() -> None:
    classifier = HeuristicSensitivityClassifier()
    assert classifier.classify(_item(f"token {AWS_KEY}")) is Sensitivity.restricted


def test_classifier_never_lowers_existing_label() -> None:
    """A clean item already labelled restricted stays restricted (never lowered)."""
    classifier = HeuristicSensitivityClassifier()
    item = _item("totally clean text", sensitivity=Sensitivity.restricted)
    assert classifier.classify(item) is Sensitivity.restricted


def test_classifier_takes_max_of_existing_and_detected() -> None:
    classifier = HeuristicSensitivityClassifier()
    # PII detected (confidential) but item already internal -> raised to confidential.
    item = _item("email bob@example.com", sensitivity=Sensitivity.internal)
    assert classifier.classify(item) is Sensitivity.confidential

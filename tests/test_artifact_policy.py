"""Tests for contextweaver.adapters.artifact_policy (issue #375)."""

from __future__ import annotations

import pytest

from contextweaver.adapters.artifact_policy import ArtifactPolicy
from contextweaver.exceptions import ConfigError


def test_defaults_are_inert() -> None:
    policy = ArtifactPolicy()
    assert policy.ttl_seconds is None
    assert policy.max_bytes is None
    assert policy.max_artifacts is None
    assert policy.redact_secrets is False


def test_from_dict_round_trip() -> None:
    policy = ArtifactPolicy.from_dict(
        {"ttl_seconds": 60, "max_bytes": 1024, "max_artifacts": 10, "redact_secrets": True}
    )
    assert policy.ttl_seconds == 60.0
    assert policy.max_bytes == 1024
    assert policy.max_artifacts == 10
    assert policy.redact_secrets is True
    assert policy.to_dict() == {
        "ttl_seconds": 60.0,
        "max_bytes": 1024,
        "max_artifacts": 10,
        "redact_secrets": True,
    }


def test_from_dict_rejects_unknown_key() -> None:
    with pytest.raises(ConfigError, match="unknown key"):
        ArtifactPolicy.from_dict({"bogus": 1})


def test_from_dict_rejects_non_mapping() -> None:
    with pytest.raises(ConfigError, match="must be a mapping"):
        ArtifactPolicy.from_dict([])  # type: ignore[arg-type]


@pytest.mark.parametrize("key", ["ttl_seconds", "max_bytes", "max_artifacts"])
def test_non_positive_numbers_rejected(key: str) -> None:
    with pytest.raises(ConfigError, match="must be positive"):
        ArtifactPolicy.from_dict({key: 0})

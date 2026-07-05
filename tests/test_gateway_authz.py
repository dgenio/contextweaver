"""Tests for contextweaver.adapters.gateway_authz (#373 / #746).

Covers the pure policy layer: rule matching precedence, the allow/deny/
require_approval verdicts, the ``GatewayError`` mapping, and config round-trips.
The runtime wiring (that a denied tool is never dispatched, that ``tool_view``
egress is gated) is covered in ``test_proxy_runtime.py``.
"""

from __future__ import annotations

import pytest

from contextweaver.adapters.gateway_authz import (
    PolicyContext,
    PolicyRule,
    ToolPolicy,
    policy_gate_error,
)
from contextweaver.exceptions import ConfigError


def _exec_ctx(**overrides: object) -> PolicyContext:
    base: dict[str, object] = {
        "meta_tool": "tool_execute",
        "tool_id": "github:create_issue@1#abcd1234",
        "namespace": "github",
        "tool_name": "create_issue",
        "read_only": False,
        "tags": ("issues",),
    }
    base.update(overrides)
    return PolicyContext(**base)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Default behaviour
# ---------------------------------------------------------------------------


def test_default_policy_allows_everything() -> None:
    policy = ToolPolicy()
    decision = policy.decide(_exec_ctx())
    assert decision.action == "allow"
    assert decision.rule_index is None
    assert policy_gate_error(policy, _exec_ctx()) is None


def test_default_deny_blocks_when_no_rule_matches() -> None:
    policy = ToolPolicy(default="deny")
    err = policy_gate_error(policy, _exec_ctx())
    assert err is not None
    assert err.code == "POLICY_DENIED"


def test_invalid_default_raises_config_error() -> None:
    with pytest.raises(ConfigError):
        ToolPolicy(default="nope")  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# Rule matching
# ---------------------------------------------------------------------------


def test_first_matching_rule_wins() -> None:
    policy = ToolPolicy(
        rules=[
            PolicyRule(action="require_approval", namespace="github"),
            PolicyRule(action="deny", namespace="github"),
        ]
    )
    decision = policy.decide(_exec_ctx())
    assert decision.action == "require_approval"
    assert decision.rule_index == 0


def test_namespace_scopes_rule() -> None:
    policy = ToolPolicy(rules=[PolicyRule(action="deny", namespace="filesystem")])
    # github tool is untouched; a filesystem tool is denied.
    assert policy.decide(_exec_ctx()).action == "allow"
    assert policy.decide(_exec_ctx(namespace="filesystem")).action == "deny"


def test_tool_glob_matches_name_and_tool_id() -> None:
    policy = ToolPolicy(rules=[PolicyRule(action="deny", tool="*delete*")])
    denied = _exec_ctx(tool_id="fs:delete_file@1#aa", tool_name="delete_file")
    assert policy.decide(denied).action == "deny"
    assert policy.decide(_exec_ctx()).action == "allow"


def test_tool_glob_is_case_sensitive() -> None:
    # Deterministic, cross-platform: fnmatchcase does not fold case.
    policy = ToolPolicy(rules=[PolicyRule(action="deny", tool="read_*")])
    assert policy.decide(_exec_ctx(tool_name="read_file", tool_id="fs:read_file")).action == "deny"
    assert policy.decide(_exec_ctx(tool_name="READ_FILE", tool_id="fs:READ_FILE")).action == "allow"


def test_tags_require_all_present() -> None:
    policy = ToolPolicy(rules=[PolicyRule(action="require_approval", tags=("destructive",))])
    assert policy.decide(_exec_ctx(tags=("destructive",))).action == "require_approval"
    assert policy.decide(_exec_ctx(tags=("issues",))).action == "allow"


def test_read_only_constraint() -> None:
    policy = ToolPolicy(rules=[PolicyRule(action="deny", read_only=False)])
    assert policy.decide(_exec_ctx(read_only=False)).action == "deny"
    assert policy.decide(_exec_ctx(read_only=True)).action == "allow"


def test_meta_tool_scopes_rule_to_one_surface() -> None:
    policy = ToolPolicy(rules=[PolicyRule(action="deny", meta_tool="tool_view")])
    # Execute is allowed; only the view surface is denied (issue #746).
    assert policy.decide(_exec_ctx()).action == "allow"
    view_ctx = PolicyContext(meta_tool="tool_view", tool_id="github:create_issue@1#abcd1234")
    assert policy.decide(view_ctx).action == "deny"


def test_empty_rule_is_catch_all() -> None:
    policy = ToolPolicy(rules=[PolicyRule(action="deny")])
    assert policy.decide(_exec_ctx()).action == "deny"
    assert policy.decide(_exec_ctx(namespace="anything", tool_name="whatever")).action == "deny"


# ---------------------------------------------------------------------------
# GatewayError mapping
# ---------------------------------------------------------------------------


def test_deny_maps_to_policy_denied_error() -> None:
    policy = ToolPolicy(rules=[PolicyRule(action="deny", reason="blocked for tests")])
    err = policy_gate_error(policy, _exec_ctx())
    assert err is not None
    assert err.code == "POLICY_DENIED"
    assert err.retryable is False
    assert err.path == "github:create_issue@1#abcd1234"
    assert err.details["meta_tool"] == "tool_execute"
    assert "blocked for tests" in err.details["reason"]


def test_require_approval_maps_to_auth_required_error() -> None:
    policy = ToolPolicy(rules=[PolicyRule(action="require_approval")])
    err = policy_gate_error(policy, _exec_ctx())
    assert err is not None
    assert err.code == "AUTH_REQUIRED"
    assert err.details["approval"] == "required"


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------


def test_roundtrip_preserves_decision() -> None:
    policy = ToolPolicy(
        default="deny",
        rules=[
            PolicyRule(action="allow", namespace="github", tool="issues.*"),
            PolicyRule(action="require_approval", tags=("destructive",), meta_tool="tool_execute"),
            PolicyRule(action="deny", tool="*delete*", read_only=False, reason="no deletes"),
        ],
    )
    restored = ToolPolicy.from_dict(policy.to_dict())
    assert restored.to_dict() == policy.to_dict()
    ctx = _exec_ctx(tool_id="fs:delete_file@1#aa", tool_name="delete_file", namespace="fs")
    assert restored.decide(ctx).action == "deny"


def test_from_dict_rejects_bad_action() -> None:
    with pytest.raises(ConfigError):
        ToolPolicy.from_dict({"rules": [{"action": "maybe"}]})


def test_from_dict_rejects_bad_meta_tool() -> None:
    with pytest.raises(ConfigError):
        ToolPolicy.from_dict({"rules": [{"action": "deny", "meta_tool": "tool_frobnicate"}]})


def test_to_dict_omits_unset_constraints() -> None:
    out = PolicyRule(action="deny", tool="*delete*").to_dict()
    assert out == {"action": "deny", "tool": "*delete*"}

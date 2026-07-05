"""Runtime authorization / policy gate for the MCP gateway (issue #373).

The two-tool gateway collapses a large upstream MCP surface into a small set of
primitives (``tool_browse`` / ``tool_execute`` / ``tool_view``).  That is good
for prompt budget, but it also concentrates access to powerful tools behind one
execution primitive.  This module adds an explicit, deterministic decision point
that :class:`~contextweaver.adapters.proxy_runtime.ProxyRuntime` consults
*before* any upstream tool call (``tool_execute``) and before returning raw
artifact bytes (``tool_view``).

Design (per the issue's maintainer guidance):

- The decision is made from the canonical ``tool_id``, the upstream
  namespace/server, the raw upstream tool name, tool metadata/annotations, the
  schema-validated arguments, and the exposure mode — never from untrusted MCP
  annotations alone (those are hints, not controls).
- The result is one of ``allow`` / ``deny`` / ``require_approval``.  ``deny`` and
  ``require_approval`` map to typed :class:`~contextweaver.adapters.gateway_error.GatewayError`
  codes (``POLICY_DENIED`` / ``AUTH_REQUIRED``) so a denied tool is never
  dispatched upstream and clients can branch without string-matching.
- Everything here is pure and deterministic (ordered rules, first match wins,
  case-sensitive globs) so policy decisions are reproducible and auditable.  The
  default is permissive (``allow``): a runtime with no policy behaves exactly as
  before, and operators opt into ``deny`` / ``require_approval`` rules.

The single :class:`ToolPolicy` governs **both** the execute and view surfaces
(issue #746), so operators express one policy that covers upstream dispatch and
raw artifact egress; scope a rule to one surface with :attr:`PolicyRule.meta_tool`.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from fnmatch import fnmatchcase
from typing import Any, Literal

from contextweaver.adapters.gateway_error import GatewayError
from contextweaver.exceptions import ConfigError

#: The policy verdict for a single evaluated call.
PolicyAction = Literal["allow", "deny", "require_approval"]

#: The gateway surface a decision applies to.  ``tool_execute`` gates upstream
#: dispatch; ``tool_view`` gates raw artifact egress (issue #746).
MetaTool = Literal["tool_execute", "tool_view"]

#: Valid policy actions, for config validation.
POLICY_ACTIONS: tuple[PolicyAction, ...] = ("allow", "deny", "require_approval")

#: Valid meta-tool surfaces, for config validation.
META_TOOLS: tuple[MetaTool, ...] = ("tool_execute", "tool_view")


@dataclass(frozen=True)
class PolicyContext:
    """The facts a :class:`ToolPolicy` decides over for one call.

    Attributes:
        meta_tool: Which surface is being gated (``tool_execute`` / ``tool_view``).
        tool_id: Canonical ``tool_id`` of the target tool (may be empty for a
            ``tool_view`` whose handle cannot be attributed to a tool).
        namespace: Upstream namespace / server the tool belongs to.
        tool_name: Raw upstream tool name (pre-namespacing).
        args: Schema-validated arguments (empty for ``tool_view``; the handle and
            selector are carried here for auditing).
        read_only: ``True`` when the tool declares no side effects.  ``tool_view``
            is always read-only.
        annotations: Untrusted upstream tool annotations (hints only).
        tags: Catalog tags on the tool (used for tag-based rules).
        exposure_mode: ``"gateway"`` or ``"transparent"``.
    """

    meta_tool: MetaTool
    tool_id: str = ""
    namespace: str = ""
    tool_name: str = ""
    args: dict[str, Any] = field(default_factory=dict)
    read_only: bool = False
    annotations: dict[str, Any] = field(default_factory=dict)
    tags: tuple[str, ...] = ()
    exposure_mode: str = "gateway"


@dataclass(frozen=True)
class PolicyDecision:
    """The outcome of evaluating a :class:`ToolPolicy` against a context.

    Attributes:
        action: ``allow`` / ``deny`` / ``require_approval``.
        reason: Short, human-readable justification (safe for diagnostics — never
            includes argument values).
        rule_index: Index of the matched rule in :attr:`ToolPolicy.rules`, or
            ``None`` when the default action was used.
    """

    action: PolicyAction
    reason: str
    rule_index: int | None = None


@dataclass(frozen=True)
class PolicyRule:
    """One ordered match → action rule.

    A rule matches a :class:`PolicyContext` when **every** constraint it declares
    is satisfied.  A rule with no constraints matches every call (a catch-all).
    Matching is deterministic and case-sensitive (``fnmatchcase``) so decisions
    are byte-identical across platforms.

    Attributes:
        action: Verdict to apply when this rule matches.
        meta_tool: Restrict the rule to one surface, or ``None`` for both.
        namespace: Exact upstream namespace to match, or ``None`` for any.
        tool: Glob matched against the canonical ``tool_id`` **and** the raw tool
            name (e.g. ``"read_*"``, ``"*delete*"``), or ``None`` for any.
        tags: Tags that must all be present on the tool, or empty for any.
        read_only: Match only read-only (``True``) or side-effecting (``False``)
            tools, or ``None`` for any.
        reason: Optional override for the decision reason.
    """

    action: PolicyAction
    meta_tool: MetaTool | None = None
    namespace: str | None = None
    tool: str | None = None
    tags: tuple[str, ...] = ()
    read_only: bool | None = None
    reason: str = ""

    def matches(self, ctx: PolicyContext) -> bool:
        """Return ``True`` when *ctx* satisfies every declared constraint."""
        if self.meta_tool is not None and self.meta_tool != ctx.meta_tool:
            return False
        if self.namespace is not None and self.namespace != ctx.namespace:
            return False
        if self.tool is not None and not (
            fnmatchcase(ctx.tool_id, self.tool) or fnmatchcase(ctx.tool_name, self.tool)
        ):
            return False
        if self.tags and not all(tag in ctx.tags for tag in self.tags):
            return False
        return self.read_only is None or self.read_only == ctx.read_only

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON/YAML-compatible dict (omitting unset constraints)."""
        out: dict[str, Any] = {"action": self.action}
        if self.meta_tool is not None:
            out["meta_tool"] = self.meta_tool
        if self.namespace is not None:
            out["namespace"] = self.namespace
        if self.tool is not None:
            out["tool"] = self.tool
        if self.tags:
            out["tags"] = list(self.tags)
        if self.read_only is not None:
            out["read_only"] = self.read_only
        if self.reason:
            out["reason"] = self.reason
        return out

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PolicyRule:
        """Deserialise from a config dict, validating shape and enum fields.

        Fails fast with :class:`~contextweaver.exceptions.ConfigError` on
        malformed input rather than silently misbehaving — e.g. a bare
        ``tags: destructive`` string (which would otherwise iterate into
        per-character tags) or a non-boolean ``read_only``.
        """
        if not isinstance(data, dict):
            raise ConfigError(f"PolicyRule entry must be a mapping, got {data!r}")
        action = data.get("action")
        if action not in POLICY_ACTIONS:
            raise ConfigError(f"PolicyRule.action must be one of {POLICY_ACTIONS}, got {action!r}")
        meta_tool = data.get("meta_tool")
        if meta_tool is not None and meta_tool not in META_TOOLS:
            raise ConfigError(
                f"PolicyRule.meta_tool must be one of {META_TOOLS} or omitted, got {meta_tool!r}"
            )
        tags = data.get("tags") or []
        # A str/bytes is iterable, so ``tags: destructive`` would become
        # per-character tags — reject it explicitly.
        if isinstance(tags, (str, bytes)) or not isinstance(tags, (list, tuple)):
            raise ConfigError(f"PolicyRule.tags must be a list of strings, got {tags!r}")
        read_only = data.get("read_only")
        if read_only is not None and not isinstance(read_only, bool):
            raise ConfigError(f"PolicyRule.read_only must be a boolean, got {read_only!r}")
        return cls(
            action=action,
            meta_tool=meta_tool,
            namespace=data.get("namespace"),
            tool=data.get("tool"),
            tags=tuple(str(tag) for tag in tags),
            read_only=read_only,
            reason=str(data.get("reason", "")),
        )


@dataclass
class ToolPolicy:
    """An ordered rule set with a default action, evaluated before dispatch.

    :meth:`decide` returns the action of the first matching rule, or
    :attr:`default` when no rule matches.  The default is ``allow`` so an
    unconfigured policy is a no-op; set ``default="deny"`` for a
    default-deny (allowlist) posture.
    """

    default: PolicyAction = "allow"
    rules: list[PolicyRule] = field(default_factory=list)

    def __post_init__(self) -> None:
        """Validate the default action at construction (issue #463 pattern)."""
        if self.default not in POLICY_ACTIONS:
            raise ConfigError(
                f"ToolPolicy.default must be one of {POLICY_ACTIONS}, got {self.default!r}"
            )

    def decide(self, ctx: PolicyContext) -> PolicyDecision:
        """Return the :class:`PolicyDecision` for *ctx* (first match wins)."""
        for index, rule in enumerate(self.rules):
            if rule.matches(ctx):
                reason = rule.reason or f"matched rule #{index} ({rule.action})"
                return PolicyDecision(action=rule.action, reason=reason, rule_index=index)
        return PolicyDecision(action=self.default, reason=f"default ({self.default})")

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON/YAML-compatible dict."""
        return {"default": self.default, "rules": [rule.to_dict() for rule in self.rules]}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ToolPolicy:
        """Deserialise from a config dict (``mcp serve --config`` ``policy`` block)."""
        default = data.get("default", "allow")
        if default not in POLICY_ACTIONS:
            raise ConfigError(
                f"ToolPolicy.default must be one of {POLICY_ACTIONS}, got {default!r}"
            )
        rules_raw = data.get("rules") or []
        if not isinstance(rules_raw, list):
            raise ConfigError(f"ToolPolicy.rules must be a list, got {rules_raw!r}")
        rules = [PolicyRule.from_dict(rule) for rule in rules_raw]
        return cls(default=default, rules=rules)


def policy_gate_error(policy: ToolPolicy, ctx: PolicyContext) -> GatewayError | None:
    """Evaluate *policy* for *ctx* and return a blocking error, or ``None``.

    Returns ``None`` when the decision is ``allow`` (the caller proceeds).  A
    ``deny`` maps to a ``POLICY_DENIED`` error; ``require_approval`` maps to an
    ``AUTH_REQUIRED`` error carrying the surface and reason in ``details`` so a
    downstream client (or a custom loop) can surface it for human approval.
    Neither dispatches upstream nor returns raw content.
    """
    decision = policy.decide(ctx)
    if decision.action == "allow":
        return None
    path = ctx.tool_id
    target = path or ctx.namespace or "?"
    if decision.action == "deny":
        return GatewayError(
            code="POLICY_DENIED",
            message=f"policy denied {ctx.meta_tool} for {target!r}: {decision.reason}",
            path=path,
            retryable=False,
            details={"meta_tool": ctx.meta_tool, "reason": decision.reason},
        )
    return GatewayError(
        code="AUTH_REQUIRED",
        message=f"{ctx.meta_tool} for {target!r} requires approval: {decision.reason}",
        path=path,
        retryable=False,
        details={"meta_tool": ctx.meta_tool, "reason": decision.reason, "approval": "required"},
    )

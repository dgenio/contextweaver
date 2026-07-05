"""Private dispatch-path helpers for :mod:`contextweaver.adapters.proxy_runtime`.

Extracted so ``proxy_runtime.py`` stays within its module-size ceiling while the
runtime authorization gate (issue #373) and its ``tool_view`` parity (issue #746)
are wired in.  Everything here is pure/deterministic and imports no transport:

- :func:`persist_result_artifacts` — post-dispatch artifact persistence that
  makes upstream results addressable via ``tool_view``.
- :func:`execute_policy_error` / :func:`view_policy_error` — evaluate the
  :class:`~contextweaver.adapters.gateway_authz.ToolPolicy` for the two gated
  surfaces and return a blocking :class:`GatewayError` (or ``None`` to proceed).
- :func:`rate_limited_error` / :func:`unverified_annotations` /
  :func:`build_dry_run_report` — small dispatch helpers lifted verbatim from the
  runtime.

Not public API.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any

from contextweaver.adapters.gateway_authz import PolicyContext, ToolPolicy, policy_gate_error
from contextweaver.adapters.gateway_controls import RateLimitDecision
from contextweaver.adapters.gateway_error import GatewayError
from contextweaver.adapters.gateway_policy import DryRunReport
from contextweaver.envelope import ResultEnvelope
from contextweaver.store.protocols import ArtifactStore
from contextweaver.types import SelectableItem


@dataclass
class UpstreamNameIndex:
    """Maps canonical ``tool_id`` → upstream raw tool name.

    Required because :func:`~contextweaver.adapters.mcp.mcp_tool_to_selectable`
    strips namespace prefixes from the canonical id (§1.4), but the upstream MCP
    server only accepts the original name.
    """

    by_tool_id: dict[str, str] = field(default_factory=dict)


def persist_result_artifacts(
    artifact_store: ArtifactStore,
    envelope: ResultEnvelope,
    binaries: dict[str, tuple[bytes, str, str]],
    full_text: str,
    tool_id: str,
) -> None:
    """Persist binary and oversized-text results so ``tool_view`` can drill in.

    Binaries are stored under their content-addressed handles; text content is
    stored under a deterministic ``text:{tool_id}:{hash}`` handle and its ref is
    appended to *envelope* so downstream ``tool_view`` calls can address it.
    Existing handles are reused (idempotent).
    """
    for handle, (data, mime, label) in binaries.items():
        if not artifact_store.exists(handle):
            artifact_store.put(handle=handle, content=data, media_type=mime, label=label)
    if full_text:
        content_bytes = full_text.encode("utf-8")
        text_hash = hashlib.sha256(content_bytes).hexdigest()[:16]
        text_handle = f"text:{tool_id}:{text_hash}"
        if artifact_store.exists(text_handle):
            text_ref = artifact_store.ref(text_handle)
        else:
            text_ref = artifact_store.put(
                handle=text_handle,
                content=content_bytes,
                media_type="text/plain",
                label=f"text result from {tool_id}",
            )
        envelope.artifacts.append(text_ref)


def unverified_annotations(raw_def: dict[str, Any]) -> dict[str, Any]:
    """Return upstream MCP annotations stamped ``verified=False`` (#483).

    The gateway never trusts upstream-declared hints (``readOnlyHint`` /
    ``destructiveHint`` / ...), so callers label them explicitly.
    """
    annotations = raw_def.get("annotations")
    out = dict(annotations) if isinstance(annotations, dict) else {}
    out["verified"] = False
    return out


def rate_limited_error(path: str, decision: RateLimitDecision) -> GatewayError:
    """Build the structured ``RATE_LIMITED`` error for a quota breach (#482)."""
    details: dict[str, Any] = {"scope": decision.scope}
    if decision.retry_after is not None:
        details["retry_after"] = round(decision.retry_after, 3)
    return GatewayError(
        code="RATE_LIMITED",
        message=f"rate limit exceeded ({decision.scope})",
        path=path,
        retryable=True,
        details=details,
    )


def build_dry_run_report(
    tool_id: str, upstream_name: str, raw_def: dict[str, Any], *, rate_allowed: bool
) -> DryRunReport:
    """Build the ``tool_execute(dry_run=True)`` report (#483) — no dispatch."""
    return DryRunReport(
        tool_id=tool_id,
        upstream_name=upstream_name,
        args_valid=True,
        annotations=unverified_annotations(raw_def),
        checks=[
            {"name": "schema_validation", "status": "pass"},
            {"name": "rate_limit", "status": "pass" if rate_allowed else "fail"},
        ],
    )


def execute_policy_error(
    policy: ToolPolicy,
    item: SelectableItem,
    *,
    tool_id: str,
    upstream_name: str,
    args: dict[str, Any],
    read_only: bool,
    raw_def: dict[str, Any],
    exposure_mode: str,
) -> GatewayError | None:
    """Return a blocking error if *policy* forbids executing *tool_id* (#373)."""
    ctx = PolicyContext(
        meta_tool="tool_execute",
        tool_id=tool_id,
        namespace=item.namespace,
        tool_name=upstream_name,
        args=args,
        read_only=read_only,
        annotations=unverified_annotations(raw_def),
        tags=tuple(item.tags),
        exposure_mode=exposure_mode,
    )
    return policy_gate_error(policy, ctx)


def view_policy_error(
    policy: ToolPolicy, handle: str, selector: dict[str, Any], *, exposure_mode: str
) -> GatewayError | None:
    """Return a blocking error if *policy* forbids raw egress of *handle* (#746).

    ``tool_view`` receives only an artifact *handle*, so tool attribution is
    best-effort: a ``text:{tool_id}:{hash}`` handle yields the canonical
    ``tool_id`` and its namespace; other handle shapes leave those blank (so only
    namespace-agnostic rules and the policy default apply).  ``tool_view`` is
    always read-only; the handle and selector are carried in ``args`` for audit.
    """
    tool_id = ""
    namespace = ""
    if handle.startswith("text:"):
        middle = handle[len("text:") :].rsplit(":", 1)[0]
        tool_id = middle
        namespace = middle.split(":", 1)[0] if ":" in middle else ""
    ctx = PolicyContext(
        meta_tool="tool_view",
        tool_id=tool_id,
        namespace=namespace,
        args={"handle": handle, "selector": selector},
        read_only=True,
        exposure_mode=exposure_mode,
    )
    return policy_gate_error(policy, ctx)

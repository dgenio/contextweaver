"""Gateway-specific instrumentation built on :mod:`contextweaver.diagnostics`."""

from __future__ import annotations

import json
import logging
from typing import Any
from uuid import uuid4

from contextweaver.adapters.gateway_catalog_diagnostics import catalog_diagnostic_summary
from contextweaver.adapters.gateway_error import GatewayError
from contextweaver.diagnostics import DiagnosticEvent, DiagnosticSink, NoOpDiagnosticSink
from contextweaver.envelope import ChoiceCard, FirewallStats, HydrationResult, ResultEnvelope
from contextweaver.tokens import count
from contextweaver.types import SelectableItem

logger = logging.getLogger("contextweaver.adapters.gateway_diagnostics")


class GatewayTelemetry:
    """Emit sanitized diagnostics for one gateway runtime session."""

    def __init__(
        self,
        sink: DiagnosticSink | None = None,
        *,
        session_id: str | None = None,
    ) -> None:
        """Create telemetry backed by *sink*."""
        self._sink = sink or NoOpDiagnosticSink()
        self.session_id = session_id or uuid4().hex

    @property
    def enabled(self) -> bool:
        """Whether diagnostics emission is active for this session."""
        return not isinstance(self._sink, NoOpDiagnosticSink)

    def emit(
        self,
        event: str,
        *,
        success: bool = True,
        duration_ms: float | None = None,
        tool_id: str | None = None,
        namespace: str | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> None:
        """Emit one event without allowing diagnostics failures to break calls."""
        try:
            self._sink.emit(
                DiagnosticEvent(
                    event=event,
                    success=success,
                    duration_ms=round(duration_ms, 3) if duration_ms is not None else None,
                    session_id=self.session_id,
                    tool_id=tool_id,
                    namespace=namespace,
                    attributes=attributes or {},
                )
            )
        except Exception:  # noqa: BLE001
            logger.exception("diagnostic sink failed while emitting %s", event)

    def catalog_registered(
        self,
        items: list[SelectableItem],
        raw_defs: dict[str, dict[str, Any]],
        *,
        mode: str,
    ) -> None:
        """Record catalog size and static schema exposure."""
        if not self.enabled:
            return
        self.emit(
            "catalog.loaded",
            attributes=catalog_diagnostic_summary(items, raw_defs, mode=mode),
        )

    def browse_completed(
        self,
        result: list[ChoiceCard] | GatewayError,
        *,
        duration_ms: float,
        query_chars: int,
        path_depth: int,
        raw_defs: dict[str, dict[str, Any]],
    ) -> None:
        """Record one browse result without recording query or path text."""
        if not self.enabled:
            return
        if isinstance(result, GatewayError):
            self.emit(
                "browse.failed",
                success=False,
                duration_ms=duration_ms,
                attributes={
                    "error_code": result.code,
                    "query_chars": query_chars,
                    "path_depth": path_depth,
                },
            )
            return
        real_cards = [card for card in result if card.id in raw_defs]
        card_tokens = sum(
            count(json.dumps(card.to_dict(), sort_keys=True, separators=(",", ":")))
            for card in real_cards
        )
        schema_tokens = sum(
            count(
                json.dumps(
                    raw_defs[card.id].get("inputSchema", {}),
                    sort_keys=True,
                    separators=(",", ":"),
                )
            )
            for card in real_cards
        )
        self.emit(
            "browse.completed",
            duration_ms=duration_ms,
            attributes={
                "query_chars": query_chars,
                "path_depth": path_depth,
                "card_count": len(result),
                "tool_ids": [card.id for card in real_cards],
                "card_tokens": card_tokens,
                "schema_tokens_avoided": max(schema_tokens - card_tokens, 0),
            },
        )

    def hydrate_completed(
        self,
        tool_id: str,
        result: HydrationResult | GatewayError,
        *,
        duration_ms: float,
        namespace: str | None,
    ) -> None:
        """Record schema hydration."""
        if not self.enabled:
            return
        if isinstance(result, GatewayError):
            self.emit(
                "hydrate.failed",
                success=False,
                duration_ms=duration_ms,
                tool_id=tool_id,
                namespace=namespace,
                attributes={"error_code": result.code},
            )
            return
        self.emit(
            "hydrate.completed",
            duration_ms=duration_ms,
            tool_id=tool_id,
            namespace=namespace,
            attributes={
                "schema_tokens": count(
                    json.dumps(result.args_schema, sort_keys=True, separators=(",", ":"))
                )
            },
        )

    def execute_failed(
        self,
        tool_id: str,
        error: GatewayError,
        *,
        duration_ms: float,
        namespace: str | None,
        arg_keys: list[str],
    ) -> None:
        """Record a failed execution."""
        if not self.enabled:
            return
        self.emit(
            "execute.failed",
            success=False,
            duration_ms=duration_ms,
            tool_id=tool_id,
            namespace=namespace,
            attributes={"error_code": error.code, "arg_keys": arg_keys},
        )

    def execute_dry_run(
        self,
        tool_id: str,
        *,
        duration_ms: float,
        namespace: str | None,
        arg_keys: list[str],
    ) -> None:
        """Record a dry-run execution (no upstream dispatch, issue #483)."""
        if not self.enabled:
            return
        self.emit(
            "execute.dry_run",
            duration_ms=duration_ms,
            tool_id=tool_id,
            namespace=namespace,
            attributes={"arg_keys": arg_keys, "dispatched": False},
        )

    def execute_cache_hit(
        self,
        tool_id: str,
        envelope: ResultEnvelope,
        *,
        duration_ms: float,
        namespace: str | None,
        arg_keys: list[str],
    ) -> None:
        """Record a read-only response-cache hit served without dispatch (#512)."""
        if not self.enabled:
            return
        self.emit(
            "execute.cache_hit",
            success=envelope.status != "error",
            duration_ms=duration_ms,
            tool_id=tool_id,
            namespace=namespace,
            attributes={
                "arg_keys": arg_keys,
                "status": envelope.status,
                "cache_hit": True,
                "dispatched": False,
            },
        )

    def execute_completed(
        self,
        tool_id: str,
        envelope: ResultEnvelope,
        *,
        duration_ms: float,
        namespace: str | None,
        arg_keys: list[str],
        full_text: str,
        binary_bytes: int,
        attempts: int = 1,
    ) -> None:
        """Attach firewall stats and record compact-result savings.

        ``attempts`` records how many upstream dispatch attempts the retry layer
        made (issue #529); ``1`` for the default single-attempt path.
        """
        original_tokens = count(full_text)
        compact_tokens = count(envelope.summary)
        artifact_ref = next(
            (ref.handle for ref in envelope.artifacts if ref.handle.startswith("text:")),
            envelope.artifacts[0].handle if envelope.artifacts else None,
        )
        triggered = len(full_text) > len(envelope.summary)
        envelope.firewall_stats = FirewallStats(
            triggered=triggered,
            strategy="summary" if triggered else "passthrough",
            original_chars=len(full_text),
            original_tokens=original_tokens,
            summary_chars=len(envelope.summary),
            summary_tokens=compact_tokens,
            artifact_ref=artifact_ref if triggered else None,
        )
        if not self.enabled:
            return
        self.emit(
            "execute.completed",
            success=envelope.status != "error",
            duration_ms=duration_ms,
            tool_id=tool_id,
            namespace=namespace,
            attributes={
                "status": envelope.status,
                "arg_keys": arg_keys,
                "raw_chars": len(full_text),
                "raw_tokens": original_tokens,
                "compact_chars": len(envelope.summary),
                "compact_tokens": compact_tokens,
                "tokens_saved": max(original_tokens - compact_tokens, 0),
                "artifact_count": len(envelope.artifacts),
                "artifact_bytes": binary_bytes
                + sum(
                    ref.size_bytes for ref in envelope.artifacts if ref.handle.startswith("text:")
                ),
                "artifact_refs": [ref.handle for ref in envelope.artifacts],
                "firewall_triggered": triggered,
                "attempts": attempts,
                "cache_hit": False,
            },
        )

    def view_completed(
        self,
        handle: str,
        selector_type: str,
        result: str | GatewayError,
        *,
        duration_ms: float,
    ) -> None:
        """Record artifact drill-down usage without recording returned content."""
        if not self.enabled:
            return
        if isinstance(result, GatewayError):
            self.emit(
                "view.failed",
                success=False,
                duration_ms=duration_ms,
                attributes={
                    "artifact_ref": handle,
                    "selector_type": selector_type,
                    "error_code": result.code,
                },
            )
            return
        self.emit(
            "view.completed",
            duration_ms=duration_ms,
            attributes={
                "artifact_ref": handle,
                "selector_type": selector_type,
                "result_chars": len(result),
                "result_tokens": count(result),
            },
        )

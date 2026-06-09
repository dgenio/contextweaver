"""Ingestion and store-write methods for :class:`ContextManager`.

Extracted from :mod:`contextweaver.context.manager` (issue #101) so the
manager stays within the project's <=300 lines per module guideline (see
AGENTS.md).  :class:`_IngestMixin` is a *partial class* of
:class:`~contextweaver.context.manager.ContextManager` — every method takes a
fully-constructed ``ContextManager`` as ``self`` and operates on its
internals.  It is not a standalone base class and is not part of the public
API; ``ContextManager`` mixes it in so the public method surface is unchanged.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from contextweaver.context import ingest as _ingest
from contextweaver.context._manager_base import _ManagerState
from contextweaver.store.episodic import Episode
from contextweaver.store.facts import Fact

if TYPE_CHECKING:
    from contextweaver.envelope import ResultEnvelope
    from contextweaver.summarize.structured import StructuredFirewall
    from contextweaver.types import ContextItem


class _IngestMixin(_ManagerState):
    """Event-log ingestion + fact/episode writes for :class:`ContextManager`."""

    # ------------------------------------------------------------------
    # Ingestion helpers
    # ------------------------------------------------------------------

    def ingest(self, item: ContextItem) -> None:
        """Append *item* to the event log.

        Args:
            item: The context item to ingest.
        """
        _ingest.ingest_item(self._event_log, item)

    def ingest_sync(self, item: ContextItem) -> None:
        """Synchronous alias for :meth:`ingest`."""
        self.ingest(item)

    async def ingest_async(self, item: ContextItem) -> None:
        """Async alias for :meth:`ingest`."""
        self.ingest(item)

    def ingest_envelope(
        self,
        tool_call_id: str,
        envelope: ResultEnvelope,
        tool_name: str = "",
    ) -> ContextItem:
        """Ingest an already-firewalled :class:`ResultEnvelope` (canonical path).

        This is the **canonical, Frame-shaped ingestion path** for the Weaver
        Stack (weaver-spec I-05).  The execution boundary (e.g. agent-kernel)
        owns firewalling and hands contextweaver a :class:`ResultEnvelope` —
        the native preimage of a weaver-spec ``Frame``.  contextweaver appends
        a summary-only :class:`ContextItem` carrying the envelope's artifact
        handle and performs budgeted selection/packing; it does **not**
        re-derive firewalling from raw output.

        weaver-spec users convert a ``Frame`` first::

            from contextweaver.adapters.weaver_contracts import from_weaver_frame
            mgr.ingest_envelope(tool_call_id, from_weaver_frame(frame))

        See :doc:`the context-firewall boundary doc </context_firewall_boundary>`
        for who firewalls what and where the seam sits.

        Args:
            tool_call_id: ID of the originating tool call.
            envelope: An already-firewalled :class:`ResultEnvelope`.
            tool_name: Human-readable tool name.

        Returns:
            The appended :class:`ContextItem` (summary text + artifact handle).
        """
        return _ingest.ingest_envelope(
            event_log=self._event_log,
            estimator=self._estimator,
            tool_call_id=tool_call_id,
            envelope=envelope,
            tool_name=tool_name,
        )

    def ingest_envelope_sync(
        self,
        tool_call_id: str,
        envelope: ResultEnvelope,
        tool_name: str = "",
    ) -> ContextItem:
        """Synchronous alias for :meth:`ingest_envelope`."""
        return self.ingest_envelope(tool_call_id, envelope, tool_name)

    def ingest_tool_result(
        self,
        tool_call_id: str,
        raw_output: str,
        tool_name: str = "",
        media_type: str = "text/plain",
        firewall_threshold: int = 2000,
        *,
        firewall: StructuredFirewall | None = None,
    ) -> tuple[ContextItem, ResultEnvelope]:
        """Ingest a *raw* tool result, running the context firewall locally.

        .. note::

            **Non-canonical for spec compliance (weaver-spec I-05).** This API
            accepts raw output and re-derives firewalling inside contextweaver.
            The canonical seam is :meth:`ingest_envelope`, where the execution
            boundary firewalls and hands over a :class:`ResultEnvelope` /
            ``Frame``.  Prefer :meth:`ingest_envelope` when integrating with an
            agent-kernel-style execution boundary; this method remains for
            standalone use where contextweaver owns the firewall.

        If the raw output exceeds *firewall_threshold* characters it is stored
        in the artifact store and the LLM sees only a summary.  Small outputs
        are also stored (with ``artifact_ref`` set) so drilldown works on all
        tool results regardless of size.

        Args:
            tool_call_id: ID of the originating tool call.
            raw_output: Raw tool output string.
            tool_name: Human-readable tool name.
            media_type: MIME type of the output.
            firewall_threshold: Character threshold above which the firewall
                stores the raw output out-of-band.
            firewall: Optional :class:`StructuredFirewall` for lossless JSON
                projection over summarisation (#406); ``deterministic`` (#404)
                applies automatically.

        Returns:
            A ``(ContextItem, ResultEnvelope)`` tuple; the item always has an
            ``artifact_ref`` and the envelope carries ``firewall_stats``.
        """
        return _ingest.ingest_tool_result(
            event_log=self._event_log,
            artifact_store=self._artifact_store,
            hook=self._hook,
            view_registry=self._view_registry,
            summarizer=self._summarizer,
            extractor=self._extractor,
            estimator=self._estimator,
            tool_call_id=tool_call_id,
            raw_output=raw_output,
            tool_name=tool_name,
            media_type=media_type,
            firewall_threshold=firewall_threshold,
            deterministic=self._deterministic,
            firewall=firewall,
        )

    def ingest_tool_result_sync(
        self,
        tool_call_id: str,
        raw_output: str,
        tool_name: str = "",
        media_type: str = "text/plain",
        firewall_threshold: int = 2000,
        *,
        firewall: StructuredFirewall | None = None,
    ) -> tuple[ContextItem, ResultEnvelope]:
        """Synchronous alias for :meth:`ingest_tool_result`."""
        return self.ingest_tool_result(
            tool_call_id,
            raw_output,
            tool_name,
            media_type,
            firewall_threshold,
            firewall=firewall,
        )

    def ingest_mcp_result(
        self,
        tool_call_id: str,
        mcp_result: dict[str, Any],
        tool_name: str,
        firewall_threshold: int = 2000,
        *,
        firewall: StructuredFirewall | None = None,
    ) -> tuple[ContextItem, ResultEnvelope]:
        """Ingest a raw MCP tool result with full artifact persistence.

        .. note::

            **Non-canonical for spec compliance (weaver-spec I-05).** Like
            :meth:`ingest_tool_result`, this accepts a raw upstream payload and
            re-derives firewalling inside contextweaver.  When the execution
            boundary already produces a :class:`ResultEnvelope` / ``Frame``, use
            :meth:`ingest_envelope` instead.  This method remains the
            happy-path for direct MCP integration where contextweaver owns the
            firewall.

        It parses the MCP result via :func:`mcp_result_to_envelope`, stores
        binary artifacts (images, resources), applies the context firewall for
        large text outputs, and appends the resulting :class:`ContextItem`.

        Args:
            tool_call_id: ID of the originating tool call.
            mcp_result: Raw MCP tool result dict (with ``content`` list).
            tool_name: Human-readable tool name.
            firewall_threshold: Character threshold above which text output
                is stored out-of-band via the firewall.
            firewall: Optional :class:`StructuredFirewall` for lossless JSON
                projection over summarisation (#406); ``deterministic`` (#404)
                applies automatically.

        Returns:
            A ``(ContextItem, ResultEnvelope)`` tuple with all artifacts
            persisted in the artifact store.
        """
        return _ingest.ingest_mcp_result(
            event_log=self._event_log,
            artifact_store=self._artifact_store,
            hook=self._hook,
            summarizer=self._summarizer,
            extractor=self._extractor,
            estimator=self._estimator,
            tool_call_id=tool_call_id,
            mcp_result=mcp_result,
            tool_name=tool_name,
            firewall_threshold=firewall_threshold,
            deterministic=self._deterministic,
            firewall=firewall,
        )

    def ingest_mcp_result_sync(
        self,
        tool_call_id: str,
        mcp_result: dict[str, Any],
        tool_name: str,
        firewall_threshold: int = 2000,
        *,
        firewall: StructuredFirewall | None = None,
    ) -> tuple[ContextItem, ResultEnvelope]:
        """Synchronous alias for :meth:`ingest_mcp_result`."""
        return self.ingest_mcp_result(
            tool_call_id, mcp_result, tool_name, firewall_threshold, firewall=firewall
        )

    # ------------------------------------------------------------------
    # Fact / episodic memory writes
    # ------------------------------------------------------------------

    def add_fact(self, key: str, value: str, metadata: dict[str, Any] | None = None) -> None:
        """Store a fact in the fact store.

        Args:
            key: Fact key.
            value: Fact value.
            metadata: Optional metadata dict.
        """
        fact_id = f"fact:{key}:{len(self._fact_store.all())}"
        self._fact_store.put(
            Fact(
                fact_id=fact_id,
                key=key,
                value=value,
                metadata=metadata or {},
            )
        )

    def add_fact_sync(self, key: str, value: str, metadata: dict[str, Any] | None = None) -> None:
        """Synchronous alias for :meth:`add_fact`."""
        self.add_fact(key, value, metadata)

    def add_episode(
        self,
        episode_id: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Store an episodic memory summary.

        Args:
            episode_id: Unique episode identifier.
            summary: Summary text.
            metadata: Optional metadata dict.
        """
        self._episodic_store.add(
            Episode(
                episode_id=episode_id,
                summary=summary,
                metadata=metadata or {},
            )
        )

    def add_episode_sync(
        self,
        episode_id: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        """Synchronous alias for :meth:`add_episode`."""
        self.add_episode(episode_id, summary, metadata)

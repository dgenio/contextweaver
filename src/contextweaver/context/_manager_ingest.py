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
from contextweaver.exceptions import DuplicateItemError, ItemNotFoundError
from contextweaver.store.episodic import Episode
from contextweaver.store.facts import Fact
from contextweaver.types import Sensitivity

if TYPE_CHECKING:
    from contextweaver.envelope import ResultEnvelope
    from contextweaver.protocols import FactStore
    from contextweaver.summarize.structured import StructuredFirewall
    from contextweaver.types import ContextItem


def _next_fact_seq(fact_store: FactStore) -> int:
    """Return the next monotonic ``add_fact`` ID suffix for *fact_store* (issue #462).

    Scans existing fact IDs **once** for the trailing ``:{int}`` suffix minted by
    :meth:`_IngestMixin.add_fact` and returns ``max + 1`` (``0`` for a fresh or
    empty store).  This seeds the per-manager counter past any IDs already in a
    pre-populated or persistent store (e.g. the ``extras/memory`` backends) so a
    counter that would otherwise restart at ``0`` across process restarts does
    not collide with existing facts.  IDs that do not end in an integer (custom
    ``FactStore.put`` callers) are ignored.
    """
    highest = -1
    for fact in fact_store.all():
        suffix = fact.fact_id.rsplit(":", 1)[-1]
        try:
            highest = max(highest, int(suffix))
        except ValueError:
            continue
    return highest + 1


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
                applies automatically.  Only takes effect when the payload
                exceeds *firewall_threshold*; smaller payloads pass through
                inline unprojected (there is nothing to offload below it).

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
            redact_secrets=self._redact_secrets,
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
                applies automatically.  Only takes effect when the payload
                exceeds *firewall_threshold*; smaller payloads pass through
                inline unprojected (there is nothing to offload below it).

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
            redact_secrets=self._redact_secrets,
            firewall=firewall,
            view_registry=self._view_registry,
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

    def add_fact(
        self,
        key: str,
        value: str,
        metadata: dict[str, Any] | None = None,
        *,
        sensitivity: Sensitivity = Sensitivity.public,
    ) -> None:
        """Store a fact in the fact store.

        The fact ID uses a monotonic per-manager counter (``fact:{key}:{seq}``)
        rather than the store's current size, so deleting a fact and adding a
        new one can never re-mint an existing ID and silently overwrite an
        unrelated fact (issue #462).  IDs stay deterministic for a fixed call
        sequence, and no full-store scan happens per call.

        On the first call the counter is seeded once past any existing
        ``add_fact``-minted IDs (see :func:`_next_fact_seq`) so a pre-populated
        or persistent store (e.g. the ``extras/memory`` backends) does not
        collide with a counter restarting at ``0`` across process restarts.

        Args:
            key: Fact key.
            value: Fact value.
            metadata: Optional metadata dict.
            sensitivity: Keyword-only sensitivity label for the fact (issue
                #450).  Defaults to :attr:`~contextweaver.types.Sensitivity.public`;
                set it higher to have the fact dropped/redacted by the header
                sensitivity enforcement when it meets the policy floor.

        Raises:
            DuplicateItemError: If the generated ID already exists.  After
                seeding this is unreachable for single-threaded use; it remains
                a defensive backstop against a concurrent writer minting the
                same ID, surfacing the clash loudly instead of relying on the
                store's insert-or-replace semantics.
        """
        if not self._fact_seq_seeded:
            self._fact_seq = _next_fact_seq(self._fact_store)
            self._fact_seq_seeded = True
        fact_id = f"fact:{key}:{self._fact_seq}"
        try:
            self._fact_store.get(fact_id)
        except ItemNotFoundError:
            pass
        else:
            raise DuplicateItemError(
                f"fact ID {fact_id!r} already exists; refusing to overwrite. Use "
                f"FactStore.put directly for intentional upsert."
            )
        self._fact_store.put(
            Fact(
                fact_id=fact_id,
                key=key,
                value=value,
                metadata=metadata or {},
                sensitivity=sensitivity,
            )
        )
        self._fact_seq += 1

    def add_fact_sync(
        self,
        key: str,
        value: str,
        metadata: dict[str, Any] | None = None,
        *,
        sensitivity: Sensitivity = Sensitivity.public,
    ) -> None:
        """Synchronous alias for :meth:`add_fact`."""
        self.add_fact(key, value, metadata, sensitivity=sensitivity)

    def add_episode(
        self,
        episode_id: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
        *,
        sensitivity: Sensitivity = Sensitivity.public,
    ) -> None:
        """Store an episodic memory summary.

        Args:
            episode_id: Unique episode identifier.
            summary: Summary text.
            metadata: Optional metadata dict.
            sensitivity: Keyword-only sensitivity label for the episode (issue
                #450).  Defaults to :attr:`~contextweaver.types.Sensitivity.public`;
                set it higher to have the summary dropped/redacted by the header
                sensitivity enforcement when it meets the policy floor.
        """
        self._episodic_store.add(
            Episode(
                episode_id=episode_id,
                summary=summary,
                metadata=metadata or {},
                sensitivity=sensitivity,
            )
        )

    def add_episode_sync(
        self,
        episode_id: str,
        summary: str,
        metadata: dict[str, Any] | None = None,
        *,
        sensitivity: Sensitivity = Sensitivity.public,
    ) -> None:
        """Synchronous alias for :meth:`add_episode`."""
        self.add_episode(episode_id, summary, metadata, sensitivity=sensitivity)

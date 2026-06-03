"""External-memory backend adapters for contextweaver (issue #195).

This sub-package hosts ``EpisodicStore`` / ``FactStore`` implementations
that delegate to external long-lived memory services (Mem0, Zep,
LangMem).  Each module imports its third-party dependency at module
load time and surfaces a friendly :class:`ImportError` (with the exact
pip command to install it) when the extra is missing.  Importing
:mod:`contextweaver.extras.memory` itself does *not* trigger those
imports — the per-backend modules are reached via attribute access
(e.g. ``from contextweaver.extras.memory.mem0 import Mem0EpisodicStore``).

Currently shipped (same protocol shape; see ``docs/integration_memory.md``):

- :mod:`contextweaver.extras.memory.mem0` — Mem0
  (``contextweaver[mem0]``); wraps a ``mem0.Memory`` scoped by ``user_id``.
- :mod:`contextweaver.extras.memory.zep` — Zep / Graphiti
  (``contextweaver[zep]``); wraps a ``zep_cloud.Zep`` scoped by ``user_id``,
  persisting items as JSON graph episodes.
- :mod:`contextweaver.extras.memory.langmem` — LangMem / LangGraph
  (``contextweaver[langmem]``); wraps any LangGraph ``BaseStore`` scoped by a
  ``namespace`` tuple.

The adapters honour the existing ``store/protocols.py`` Protocols
without widening them — methods that an upstream backend cannot
implement raise :class:`NotImplementedError` with a clear message
pointing at the closest natively-supported call.
"""

from __future__ import annotations

"""External-memory backend adapters for contextweaver (issue #195).

This sub-package hosts ``EpisodicStore`` / ``FactStore`` implementations
that delegate to external long-lived memory services (Mem0, Zep,
LangMem).  Each module imports its third-party dependency at module
load time and surfaces a friendly :class:`ImportError` (with the exact
pip command to install it) when the extra is missing.  Importing
:mod:`contextweaver.extras.memory` itself does *not* trigger those
imports — the per-backend modules are reached via attribute access
(e.g. ``from contextweaver.extras.memory.mem0 import Mem0EpisodicStore``).

Currently shipped:

- :mod:`contextweaver.extras.memory.mem0` — Mem0
  (``contextweaver[mem0]``).

Planned (same protocol shape; see ``docs/integration_memory.md``):

- Zep / Graphiti (``contextweaver[zep]``)
- LangMem (``contextweaver[langmem]``)

The adapters honour the existing ``store/protocols.py`` Protocols
without widening them — methods that an upstream backend cannot
implement raise :class:`NotImplementedError` with a clear message
pointing at the closest natively-supported call.
"""

from __future__ import annotations

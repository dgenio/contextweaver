"""Optional integrations gated behind ``[project.optional-dependencies]`` extras.

Each module in this package imports its third-party dependency at module
import time and surfaces a friendly ``ImportError`` (with the exact pip
command to install it) when the extra is missing. Importing
``contextweaver.extras`` itself does *not* trigger those imports.

Currently shipped:

- :mod:`contextweaver.extras.otel` — OpenTelemetry tracing + metrics
  (``contextweaver[otel]``).
- :mod:`contextweaver.extras.embeddings` — sentence-transformers embedding
  backend + hybrid embedding/TF-IDF retriever (``contextweaver[embeddings]``,
  issue #8).
- :mod:`contextweaver.extras.llm_summarizer` — optional LLM-backed
  ``Summarizer`` / ``Extractor`` plugins for the context firewall; **no extra
  required** (the caller supplies its own model call function, issue #26).
- :mod:`contextweaver.extras.memory` — external long-lived memory backends
  implementing ``EpisodicStore`` / ``FactStore``: ``mem0`` (``[mem0]``),
  ``zep`` (``[zep]``), and ``langmem`` (``[langmem]``).  See the sub-package
  for the layout (issue #195).
"""

from __future__ import annotations

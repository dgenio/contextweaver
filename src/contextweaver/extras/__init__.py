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
- :mod:`contextweaver.extras.memory.mem0` — Mem0 ``EpisodicStore`` /
  ``FactStore`` implementations (``contextweaver[mem0]``).  See
  :mod:`contextweaver.extras.memory` for the external-memory backend
  sub-package layout (issue #195).
"""

from __future__ import annotations

"""Optional integrations gated behind ``[project.optional-dependencies]`` extras.

Each module in this package imports its third-party dependency at module
import time and surfaces a friendly ``ImportError`` (with the exact pip
command to install it) when the extra is missing. Importing
``contextweaver.extras`` itself does *not* trigger those imports.

Currently shipped:

- :mod:`contextweaver.extras.otel` — OpenTelemetry tracing + metrics
  (``contextweaver[otel]``).
"""

from __future__ import annotations

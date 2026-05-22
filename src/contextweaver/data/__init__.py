"""Packaged data files shipped with the contextweaver wheel.

Re-exports the data-path helpers from :mod:`contextweaver.data._paths`.

Currently ships:

- ``mcp_gateway_catalog.yaml`` — 60-tool MCP Context Gateway reference
  catalog used by the ``contextweaver demo --scenario mcp-gateway-full``
  CLI scenario (issue #264) and by the
  ``examples/architectures/mcp_context_gateway/`` reference architecture.
  Single source of truth — the example imports
  :func:`gateway_catalog_path` rather than carrying its own copy.
"""

from contextweaver.data._paths import GATEWAY_CATALOG_FILENAME, gateway_catalog_path

__all__ = ["GATEWAY_CATALOG_FILENAME", "gateway_catalog_path"]

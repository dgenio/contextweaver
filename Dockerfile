# syntax=docker/dockerfile:1
#
# contextweaver — MCP Context Gateway (stdio) image.
#
# Boots contextweaver in *gateway* mode over stdio: an MCP client / inspector
# sees a bounded set of meta-tools (tool_browse / tool_execute / tool_view)
# instead of a full upstream tool catalog, plus an artifact-backed result
# firewall. The image is self-contained — it fronts the packaged 60-tool
# reference catalog, so it starts and answers MCP `initialize` + `tools/list`
# with no external configuration. That is exactly what an automated scanner
# (e.g. Glama) needs to introspect and score the server.
#
#   Build:  docker build -t contextweaver-gateway .
#   Run:    docker run --rm -i contextweaver-gateway        # speak MCP over stdio
#
# To front your own upstream catalog instead of the bundled reference one,
# mount it and append a --catalog override (the last --catalog wins):
#   docker run --rm -i -v "$PWD/catalog.json:/app/catalog.json" \
#       contextweaver-gateway --catalog /app/catalog.json

FROM python:3.12-slim

# stdio MCP transport must not buffer; keep the image quiet and reproducible.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install contextweaver from source — matches the repo being scanned and needs
# no published release. Core deps only (tiktoken, PyYAML, rank-bm25, mcp,
# jsonschema, typer, rich); the gateway requires no optional extras, and all
# core deps ship manylinux/pure-python wheels so no compiler is needed.
COPY . /app
RUN pip install .

# Expose the packaged reference catalog at a stable path (copied from the
# source tree so this does not depend on wheel package-data), and fail the
# build early if the gateway config does not validate.
RUN cp /app/src/contextweaver/data/mcp_gateway_catalog.yaml /app/catalog.yaml \
    && contextweaver mcp serve --gateway --catalog /app/catalog.yaml --dry-run

# Run the long-lived server process unprivileged.
RUN useradd --create-home --uid 10001 app && chown -R app:app /app
USER app

# Speak MCP in gateway mode over stdio. --quiet keeps stdout reserved for the
# protocol (lifecycle logs are suppressed). Extra `docker run` arguments are
# appended to this command, so `--catalog /path` overrides the bundled catalog.
ENTRYPOINT ["contextweaver", "mcp", "serve", "--gateway", "--catalog", "/app/catalog.yaml", "--quiet"]

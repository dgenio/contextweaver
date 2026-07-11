# Official Docker image

The gateway ships as a multi-arch (`linux/amd64`, `linux/arm64`) image built
from the repository `Dockerfile` and published to GHCR on every release by
`.github/workflows/docker-publish.yml` (issue #432):

```text
ghcr.io/dgenio/contextweaver:<version>
ghcr.io/dgenio/contextweaver:latest
```

## Run

The image is self-contained — it fronts the packaged reference catalog and
speaks MCP gateway mode over stdio:

```bash
docker run --rm -i ghcr.io/dgenio/contextweaver:latest
```

Front your own catalog or live upstreams by mounting a config
(extra arguments are appended to the entrypoint, last flag wins):

```bash
docker run --rm -i \
  -v "$PWD/gateway.yaml:/app/gateway.yaml" \
  ghcr.io/dgenio/contextweaver:latest --config /app/gateway.yaml
```

Note that `upstreams:` entries with `type: stdio` launch processes *inside*
the container — the upstream command must exist in the image or a derived
image; `type: http` / `type: sse` upstreams need network reachability from
the container.

## MCP client entry

```json
{
  "mcpServers": {
    "contextweaver-gateway": {
      "command": "docker",
      "args": ["run", "--rm", "-i", "ghcr.io/dgenio/contextweaver:latest"]
    }
  }
}
```

## Docker MCP Toolkit listing

The image carries the `io.modelcontextprotocol.server.name` OCI label
matching `server.json` (`io.github.dgenio/contextweaver`), which the Docker
MCP catalog uses to associate image and registry entry. Listing in the Docker
MCP Toolkit is an external submission against
[docker/mcp-registry](https://github.com/docker/mcp-registry); the
submission checklist lives with the release process:

1. Release published → `docker-publish.yml` pushed the version tag.
2. `server.json` version matches the released package version.
3. Submit/refresh the entry in `docker/mcp-registry` pointing at the GHCR
   image and this page.

## Verify a pulled image

```bash
docker run --rm -i ghcr.io/dgenio/contextweaver:latest --dry-run
```

prints the catalog/tool summary and exits without binding stdio. Release
images are built with provenance attestation enabled; verify with
`docker buildx imagetools inspect` or `gh attestation verify`.

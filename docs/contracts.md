# Contracts — JSON Schemas for contextweaver public types

> **Status:** stable as of v0.5 ([#225][i225])
> **Closes:** [#196][i196]

contextweaver publishes JSON Schemas (Draft 2020-12) for every public
on-the-wire type so downstream tools — IDEs, catalog linters, language
ports, evaluators, observability platforms — can program against a
machine-readable contract instead of reverse-engineering the dataclasses.

Schemas are **generated from the dataclasses** by
`scripts/gen_schemas.py` (engine: `src/contextweaver/_schema_gen.py`)
and committed under `schemas/`. A CI gate (`make schemas-check`, wired
into `make ci` and `.github/workflows/ci.yml`) fails the build if a
dataclass field is renamed without regenerating the schemas.

## Published schemas

| Schema file | Dataclass | Purpose |
|---|---|---|
| `schemas/catalog.schema.json` | `SelectableItem[]` (`src/contextweaver/types.py`) | Validates catalog JSON / YAML files (`examples/sample_catalog.yaml`). |
| `schemas/choice_card.schema.json` | `ChoiceCard` (`src/contextweaver/envelope.py`) | Validates rendered choice cards. Carries the gateway-spec §2 size bounds (`name` ≤ 64 chars, ≤ 5 tags each ≤ 24 chars, `kind` ∈ `tool` / `agent` / `skill` / `internal`). |
| `schemas/result_envelope.schema.json` | `ResultEnvelope` (`src/contextweaver/envelope.py`) | Validates tool-result envelopes (summary, facts, artifacts, views, provenance). |
| `schemas/route_trace.schema.json` | `RouteTrace` (`src/contextweaver/routing/trace.py`) | Validates structured routing audit records (issue #51). |
| `schemas/build_stats.schema.json` | `BuildStats` (`src/contextweaver/envelope.py`) | Validates diagnostic build statistics (issue #106). |
| `schemas/graph_manifest.schema.json` | `GraphManifest` (`src/contextweaver/routing/manifest.py`) | Validates routing-graph build manifests (issues #15, #48). |

Each schema is also published at a stable `$id` URL under the docs site:

```
https://dgenio.github.io/contextweaver/schemas/v0/<name>.schema.json
```

## Versioning policy

Schemas are versioned independently of the library:

- All current schemas live under `/v0/`.
- Backwards-incompatible changes to any schema bump that schema to `/v1/`
  in its own deliberate PR (and `/v0/` stays published for at least one
  minor cycle).
- Additive changes (new optional fields, new enum values) stay under the
  current version.

The library `version` (`pyproject.toml`) and the schema `/vN/` path
move independently — a library minor bump does **not** automatically
imply a schema major bump.

## Regenerating

```bash
make schemas        # regenerate all 6 schemas + copy to docs/schemas/v0/
make schemas-check  # exit non-zero on drift (gating CI step)
```

`make schemas-check` is part of `make ci` and runs in
`.github/workflows/ci.yml`, so any PR that renames a dataclass field
without regenerating the schemas will fail CI.

## Using the schemas — VS Code YAML autocomplete

The `examples/sample_catalog.yaml` file carries a top-of-file
`# yaml-language-server: $schema=...` header:

```yaml
# yaml-language-server: $schema=https://dgenio.github.io/contextweaver/schemas/v0/catalog.schema.json
- args_schema: {}
  cost_hint: 0.29
  description: Export audit log entries
  id: admin.audit.export
  kind: tool
  ...
```

With the [Red Hat YAML extension][redhat-yaml] installed, VS Code now
tab-completes `SelectableItem` fields, flags typos, and surfaces the
size bounds (e.g. `tags` ≤ 5 entries) inline.

## Using the schemas — programmatic validation

```python
import json
import jsonschema

with open("schemas/choice_card.schema.json") as f:
    schema = json.load(f)

with open("some_card.json") as f:
    instance = json.load(f)

jsonschema.validate(instance, schema)  # raises on contract violation
```

contextweaver ships `jsonschema>=4.0` as a core dependency
(`pyproject.toml`), so no extra install is needed in downstream Python
code that already depends on contextweaver.

## Round-trip guarantee

For every published schema, the following round-trip holds:

```python
instance = TheDataclass(...)               # construct
payload = instance.to_dict()               # serialise
jsonschema.validate(payload, the_schema)   # validates clean
restored = TheDataclass.from_dict(payload) # deserialise
assert restored == instance                # equal
```

`tests/test_schema_gen.py` enforces this for all 6 schemas.

[i225]: https://github.com/dgenio/contextweaver/issues/225
[i196]: https://github.com/dgenio/contextweaver/issues/196
[redhat-yaml]: https://marketplace.visualstudio.com/items?itemName=redhat.vscode-yaml

#!/usr/bin/env python3
"""Regenerate ``schemas/*.schema.json`` from contextweaver dataclasses (issue #225).

Six published schemas:

- ``catalog.schema.json`` — array of :class:`~contextweaver.types.SelectableItem`.
- ``choice_card.schema.json`` — :class:`~contextweaver.envelope.ChoiceCard`
  (carries the gateway-spec §2 size bounds as ``maxLength`` / ``maxItems``).
- ``result_envelope.schema.json`` — :class:`~contextweaver.envelope.ResultEnvelope`.
- ``route_trace.schema.json`` — :class:`~contextweaver.routing.trace.RouteTrace`.
- ``build_stats.schema.json`` — :class:`~contextweaver.envelope.BuildStats`.
- ``graph_manifest.schema.json`` — :class:`~contextweaver.routing.manifest.GraphManifest`.

Usage::

    python scripts/gen_schemas.py            # regenerate all 6 + copy to docs/
    python scripts/gen_schemas.py --check    # exit non-zero on drift

The ``--check`` mode is the engine of ``make schemas-check`` and is wired
into ``make ci``.  CI re-runs it on every PR so dataclass-field drift cannot
ship without the schema being regenerated.
"""

from __future__ import annotations

import argparse
from collections.abc import Sequence
from pathlib import Path

from _golden import check_text_artifacts, write_text_artifacts

from contextweaver._schema_gen import (
    SCHEMA_ID_BASE,
    generate_array_schema,
    generate_schema,
    schema_to_json,
)
from contextweaver.envelope import BuildStats, ChoiceCard, ResultEnvelope
from contextweaver.routing.manifest import GraphManifest
from contextweaver.routing.trace import RouteTrace
from contextweaver.types import SelectableItem

REPO_ROOT = Path(__file__).resolve().parent.parent
SCHEMAS_DIR = REPO_ROOT / "schemas"
DOCS_SCHEMAS_DIR = REPO_ROOT / "docs" / "schemas" / "v0"


def _build_schemas() -> dict[str, str]:
    """Return ``{relative_path: serialised_json}`` for every schema."""
    schemas: dict[str, str] = {}
    schemas["catalog.schema.json"] = schema_to_json(
        generate_array_schema(
            SelectableItem,
            schema_id=f"{SCHEMA_ID_BASE}/catalog.schema.json",
            title="contextweaver catalog (SelectableItem[])",
        )
    )
    schemas["choice_card.schema.json"] = schema_to_json(
        generate_schema(ChoiceCard, schema_id=f"{SCHEMA_ID_BASE}/choice_card.schema.json")
    )
    schemas["result_envelope.schema.json"] = schema_to_json(
        generate_schema(ResultEnvelope, schema_id=f"{SCHEMA_ID_BASE}/result_envelope.schema.json")
    )
    schemas["route_trace.schema.json"] = schema_to_json(
        generate_schema(RouteTrace, schema_id=f"{SCHEMA_ID_BASE}/route_trace.schema.json")
    )
    schemas["build_stats.schema.json"] = schema_to_json(
        generate_schema(BuildStats, schema_id=f"{SCHEMA_ID_BASE}/build_stats.schema.json")
    )
    schemas["graph_manifest.schema.json"] = schema_to_json(
        generate_schema(GraphManifest, schema_id=f"{SCHEMA_ID_BASE}/graph_manifest.schema.json")
    )
    return schemas


def _artifact_map() -> dict[Path, str]:
    """Return ``{absolute_path: serialised_json}`` for ``schemas/`` and the
    mkdocs mirror under ``docs/schemas/v0/`` (published at the ``$id`` URL)."""
    rendered: dict[Path, str] = {}
    for name, body in _build_schemas().items():
        rendered[SCHEMAS_DIR / name] = body
        rendered[DOCS_SCHEMAS_DIR / name] = body
    return rendered


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero on drift instead of writing schemas.",
    )
    args = parser.parse_args(argv)

    rendered = _artifact_map()

    if args.check:
        return check_text_artifacts(rendered, label="schemas", regen="make schemas")

    write_text_artifacts(rendered)
    # rendered holds each schema twice (schemas/ + the docs/schemas/v0 mirror);
    # report the distinct schema count to avoid a confusing doubled number.
    print(f"wrote {len(rendered) // 2} schemas to schemas/ + docs/schemas/v0/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

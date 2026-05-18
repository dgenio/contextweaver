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
import shutil
import sys
from collections.abc import Sequence
from pathlib import Path

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


def _write(schemas: dict[str, str]) -> None:
    SCHEMAS_DIR.mkdir(parents=True, exist_ok=True)
    DOCS_SCHEMAS_DIR.mkdir(parents=True, exist_ok=True)
    for name, body in schemas.items():
        (SCHEMAS_DIR / name).write_text(body, encoding="utf-8")
    # Mirror under docs/ so mkdocs publishes them at the $id URL.
    for name in schemas:
        shutil.copyfile(SCHEMAS_DIR / name, DOCS_SCHEMAS_DIR / name)


def _check(schemas: dict[str, str]) -> int:
    drifted: list[str] = []
    for name, expected in schemas.items():
        on_disk = SCHEMAS_DIR / name
        if not on_disk.exists() or on_disk.read_text(encoding="utf-8") != expected:
            drifted.append(name)
        mirror = DOCS_SCHEMAS_DIR / name
        if not mirror.exists() or mirror.read_text(encoding="utf-8") != expected:
            drifted.append(f"docs/schemas/v0/{name}")
    if drifted:
        print("schemas drifted — run `make schemas`:", file=sys.stderr)
        for path in drifted:
            print(f"  {path}", file=sys.stderr)
        return 1
    print("schemas up to date")
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero on drift instead of writing schemas.",
    )
    args = parser.parse_args(argv)

    schemas = _build_schemas()

    if args.check:
        return _check(schemas)

    _write(schemas)
    print(f"wrote {len(schemas)} schemas to {SCHEMAS_DIR.relative_to(REPO_ROOT)}/")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

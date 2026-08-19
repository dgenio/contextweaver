"""Minimal offline capability snapshot + semantic-diff experiment.

This module intentionally does not participate in ContextWeaver's runtime,
routing, gateway, memory, or context-building paths.  It exists to test the D1
product hypothesis from issue #856 with the smallest possible integration
surface:

    source -> deterministic snapshot -> inspect -> diff -> verify

Run it as ``python -m contextweaver.d1``.  Keeping the experiment in its own
module avoids turning an unvalidated product hypothesis into a new permanent
public API or top-level CLI contract before external evidence exists.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from pathlib import Path
from typing import Any, Iterable, Sequence

from contextweaver.adapters.mcp import mcp_tool_to_selectable
from contextweaver.adapters.openapi import load_openapi_catalog
from contextweaver.exceptions import CatalogError
from contextweaver.routing.catalog import load_catalog
from contextweaver.types import SelectableItem

SNAPSHOT_SCHEMA = "contextweaver.capability-snapshot@1"


def _canonical_bytes(value: Any) -> bytes:
    """Return stable UTF-8 JSON bytes for hashing and deterministic output."""
    return json.dumps(
        value,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _read_json(path: Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise CatalogError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CatalogError(f"invalid JSON in {path}: {exc}") from exc


def _load_mcp_snapshot(path: Path) -> list[SelectableItem]:
    """Load a captured MCP ``tools/list`` payload without executing tools."""
    raw = _read_json(path)
    if isinstance(raw, dict):
        tools = raw.get("tools")
    else:
        tools = raw
    if not isinstance(tools, list) or not tools:
        raise CatalogError("MCP snapshot must contain a non-empty 'tools' list")
    items: list[SelectableItem] = []
    for index, tool in enumerate(tools):
        if not isinstance(tool, dict):
            raise CatalogError(f"MCP tool at index {index} is not an object")
        items.append(mcp_tool_to_selectable(tool))
    return items


def _load_source(path: Path, source_type: str) -> list[SelectableItem]:
    if source_type == "native":
        return load_catalog(path, on_invalid="raise")
    if source_type == "mcp":
        return _load_mcp_snapshot(path)
    if source_type == "openapi":
        return load_openapi_catalog(path).all()
    raise CatalogError(f"unsupported source type: {source_type!r}")


def _capabilities(items: Iterable[SelectableItem]) -> list[dict[str, Any]]:
    payload = [item.to_dict() for item in items]
    payload.sort(key=lambda item: str(item.get("id", "")))
    ids = [str(item.get("id", "")) for item in payload]
    if any(not item_id for item_id in ids):
        raise CatalogError("normalized capability has an empty id")
    duplicates = sorted({item_id for item_id in ids if ids.count(item_id) > 1})
    if duplicates:
        raise CatalogError(f"duplicate normalized capability ids: {duplicates}")
    return payload


def build_snapshot(path: Path, source_type: str) -> dict[str, Any]:
    """Build one deterministic snapshot from an already-available source file."""
    try:
        source_bytes = path.read_bytes()
    except OSError as exc:
        raise CatalogError(f"cannot read {path}: {exc}") from exc
    capabilities = _capabilities(_load_source(path, source_type))
    return {
        "schema": SNAPSHOT_SCHEMA,
        "source": {
            "type": source_type,
            # Deliberately omit local path/hostname/timestamps: identical source
            # bytes must produce an identical snapshot on another machine.
            "content_digest": _sha256(source_bytes),
        },
        "capability_digest": _sha256(_canonical_bytes(capabilities)),
        "capabilities": capabilities,
    }


def _validate_snapshot(snapshot: Any) -> list[str]:
    problems: list[str] = []
    if not isinstance(snapshot, dict):
        return ["snapshot must be a JSON object"]
    if snapshot.get("schema") != SNAPSHOT_SCHEMA:
        problems.append(f"schema must be {SNAPSHOT_SCHEMA!r}")
    source = snapshot.get("source")
    if not isinstance(source, dict):
        problems.append("source must be an object")
    else:
        if source.get("type") not in {"native", "mcp", "openapi"}:
            problems.append("source.type must be native, mcp, or openapi")
        digest = source.get("content_digest")
        if not isinstance(digest, str) or not digest.startswith("sha256:"):
            problems.append("source.content_digest must be a sha256 digest")
    capabilities = snapshot.get("capabilities")
    if not isinstance(capabilities, list):
        problems.append("capabilities must be a list")
        return problems
    ids: list[str] = []
    for index, capability in enumerate(capabilities):
        if not isinstance(capability, dict):
            problems.append(f"capabilities[{index}] must be an object")
            continue
        item_id = capability.get("id")
        if not isinstance(item_id, str) or not item_id:
            problems.append(f"capabilities[{index}].id must be a non-empty string")
        else:
            ids.append(item_id)
    if ids != sorted(ids):
        problems.append("capabilities must be sorted by id")
    if len(ids) != len(set(ids)):
        problems.append("capability ids must be unique")
    expected = _sha256(_canonical_bytes(capabilities))
    if snapshot.get("capability_digest") != expected:
        problems.append("capability_digest does not match capabilities")
    return problems


def load_snapshot(path: Path) -> dict[str, Any]:
    raw = _read_json(path)
    problems = _validate_snapshot(raw)
    if problems:
        raise CatalogError("invalid capability snapshot: " + "; ".join(problems))
    assert isinstance(raw, dict)
    return raw


def inspect_snapshot(snapshot: dict[str, Any]) -> dict[str, Any]:
    capabilities = snapshot["capabilities"]
    return {
        "schema": snapshot["schema"],
        "source": dict(snapshot["source"]),
        "capability_digest": snapshot["capability_digest"],
        "capability_count": len(capabilities),
        "capability_ids": [capability["id"] for capability in capabilities],
    }


def _changed_paths(before: Any, after: Any, prefix: str = "") -> list[str]:
    """Return deterministic JSON-pointer-like paths whose values differ."""
    if type(before) is not type(after):
        return [prefix or "/"]
    if isinstance(before, dict):
        paths: list[str] = []
        for key in sorted(set(before) | set(after)):
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            child = f"{prefix}/{escaped}"
            if key not in before or key not in after:
                paths.append(child)
            else:
                paths.extend(_changed_paths(before[key], after[key], child))
        return paths
    if isinstance(before, list):
        if before == after:
            return []
        # Lists such as JSON-Schema ``required``/``enum`` are intentionally
        # reported as a single semantic field instead of unstable index diffs.
        return [prefix or "/"]
    return [] if before == after else [prefix or "/"]


def _classify_change(paths: Sequence[str]) -> str:
    """Classify without pretending to solve general schema compatibility."""
    if paths and all(path.startswith("/description") for path in paths):
        return "documentation_only"
    if any(
        path.startswith("/args_schema")
        or path.startswith("/output_schema")
        or path.startswith("/constraints")
        for path in paths
    ):
        return "contract_changed"
    return "metadata_or_behavior_changed"


def _risk(paths: Sequence[str]) -> str:
    """Flag schema changes that commonly require compatibility review."""
    contract_paths = [
        path
        for path in paths
        if path.startswith("/args_schema") or path.startswith("/output_schema")
    ]
    if not contract_paths:
        return "none"
    if any(
        path.endswith("/required")
        or "/required/" in path
        or path.endswith("/type")
        or "/type/" in path
        or path.endswith("/enum")
        or "/enum/" in path
        for path in contract_paths
    ):
        return "potentially_breaking"
    return "review_required"


def diff_snapshots(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_map = {item["id"]: item for item in before["capabilities"]}
    after_map = {item["id"]: item for item in after["capabilities"]}
    added = sorted(set(after_map) - set(before_map))
    removed = sorted(set(before_map) - set(after_map))
    changed: list[dict[str, Any]] = []
    for item_id in sorted(set(before_map) & set(after_map)):
        paths = _changed_paths(before_map[item_id], after_map[item_id])
        if paths:
            changed.append(
                {
                    "id": item_id,
                    "classification": _classify_change(paths),
                    "risk": _risk(paths),
                    "paths": paths,
                }
            )
    return {
        "schema": "contextweaver.capability-diff@1",
        "before_digest": before["capability_digest"],
        "after_digest": after["capability_digest"],
        "source_changed": before["source"] != after["source"],
        "added": added,
        "removed": removed,
        "changed": changed,
        "has_changes": bool(added or removed or changed),
    }


def _write_json(path: Path | None, payload: dict[str, Any]) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if path is None:
        sys.stdout.write(text)
    else:
        path.write_text(text, encoding="utf-8")


def _cmd_snapshot(args: argparse.Namespace) -> int:
    snapshot = build_snapshot(args.source, args.source_type)
    _write_json(args.output, snapshot)
    return 0


def _cmd_inspect(args: argparse.Namespace) -> int:
    _write_json(None, inspect_snapshot(load_snapshot(args.snapshot)))
    return 0


def _cmd_diff(args: argparse.Namespace) -> int:
    report = diff_snapshots(load_snapshot(args.before), load_snapshot(args.after))
    _write_json(None, report)
    return 1 if args.check and report["has_changes"] else 0


def _cmd_verify(args: argparse.Namespace) -> int:
    raw = _read_json(args.snapshot)
    problems = _validate_snapshot(raw)
    payload = {"ok": not problems, "problems": problems}
    _write_json(None, payload)
    return 0 if not problems else 1


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m contextweaver.d1",
        description="Offline D1 experiment: snapshot, inspect, diff and verify capability surfaces.",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    snapshot = sub.add_parser("snapshot", help="Normalize a source into a deterministic snapshot.")
    snapshot.add_argument("source", type=Path)
    snapshot.add_argument(
        "--source-type",
        required=True,
        choices=("native", "mcp", "openapi"),
        help="Input format; explicit by design so format inference cannot silently change semantics.",
    )
    snapshot.add_argument("--output", "-o", required=True, type=Path)
    snapshot.set_defaults(func=_cmd_snapshot)

    inspect = sub.add_parser("inspect", help="Show a compact snapshot receipt.")
    inspect.add_argument("snapshot", type=Path)
    inspect.set_defaults(func=_cmd_inspect)

    diff = sub.add_parser("diff", help="Report semantic paths changed between two snapshots.")
    diff.add_argument("before", type=Path)
    diff.add_argument("after", type=Path)
    diff.add_argument(
        "--check",
        action="store_true",
        help="Exit 1 when a capability change is present; useful as an explicit CI gate.",
    )
    diff.set_defaults(func=_cmd_diff)

    verify = sub.add_parser("verify", help="Verify snapshot structure and capability digest.")
    verify.add_argument("snapshot", type=Path)
    verify.set_defaults(func=_cmd_verify)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        return int(args.func(args))
    except (CatalogError, OSError, ValueError) as exc:
        sys.stderr.write(f"contextweaver d1: {exc}\n")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())

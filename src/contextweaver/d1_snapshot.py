"""Pure offline snapshot and semantic-diff core for the D1 experiment (#856)."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any, cast

from contextweaver.adapters.mcp import mcp_tool_to_selectable
from contextweaver.adapters.openapi import load_openapi_catalog
from contextweaver.exceptions import CatalogError
from contextweaver.routing.catalog import load_catalog
from contextweaver.types import SelectableItem

SNAPSHOT_SCHEMA = "contextweaver.capability-snapshot@1"


def _canonical_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode(
        "utf-8"
    )


def _sha256(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _read_json(path: Path) -> object:
    try:
        return cast(object, json.loads(path.read_text(encoding="utf-8")))
    except OSError as exc:
        raise CatalogError(f"cannot read {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise CatalogError(f"invalid JSON in {path}: {exc}") from exc


def _load_mcp_snapshot(path: Path) -> list[SelectableItem]:
    """Load a captured MCP ``tools/list`` payload without executing tools."""
    raw = _read_json(path)
    tools = raw.get("tools") if isinstance(raw, dict) else raw
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


def _logical_id(item: SelectableItem, source_type: str) -> str:
    """Return identity suitable for comparing the same logical capability.

    The existing MCP routing ID includes input-schema identity. D1 compares MCP
    tools by upstream name instead, retaining the routing ID as ``normalized_id``
    so a schema edit is one contract change rather than an apparent remove+add.
    """
    return f"mcp:{item.name}" if source_type == "mcp" else item.id


def _capabilities(items: Iterable[SelectableItem], source_type: str) -> list[dict[str, Any]]:
    payload: list[dict[str, Any]] = []
    for item in items:
        record = item.to_dict()
        normalized_id = str(record.get("id", ""))
        logical_id = _logical_id(item, source_type)
        record["id"] = logical_id
        if normalized_id and normalized_id != logical_id:
            record["normalized_id"] = normalized_id
        payload.append(record)
    payload.sort(key=lambda item: str(item.get("id", "")))
    ids = [str(item.get("id", "")) for item in payload]
    if any(not item_id for item_id in ids):
        raise CatalogError("normalized capability has an empty id")
    duplicates = sorted({item_id for item_id in ids if ids.count(item_id) > 1})
    if duplicates:
        raise CatalogError(f"duplicate logical capability ids: {duplicates}")
    return payload


def build_snapshot(path: Path, source_type: str) -> dict[str, Any]:
    """Build a path-independent deterministic snapshot from a source file."""
    try:
        source_bytes = path.read_bytes()
    except OSError as exc:
        raise CatalogError(f"cannot read {path}: {exc}") from exc
    capabilities = _capabilities(_load_source(path, source_type), source_type)
    return {
        "schema": SNAPSHOT_SCHEMA,
        "source": {"type": source_type, "content_digest": _sha256(source_bytes)},
        "capability_digest": _sha256(_canonical_bytes(capabilities)),
        "capabilities": capabilities,
    }


def _validate_snapshot(snapshot: object) -> list[str]:
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
    if not isinstance(raw, dict):
        raise CatalogError("invalid capability snapshot: snapshot must be a JSON object")
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


def _changed_paths(before: object, after: object, prefix: str = "") -> list[str]:
    """Return deterministic JSON-pointer-like paths whose values differ."""
    if type(before) is not type(after):
        return [prefix or "/"]
    if isinstance(before, dict) and isinstance(after, dict):
        paths: list[str] = []
        for key in sorted(set(before) | set(after), key=str):
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            child = f"{prefix}/{escaped}"
            if key not in before or key not in after:
                paths.append(child)
            else:
                paths.extend(_changed_paths(before[key], after[key], child))
        return paths
    if isinstance(before, list) and isinstance(after, list):
        return [] if before == after else [prefix or "/"]
    return [] if before == after else [prefix or "/"]


def _classify_change(paths: Sequence[str]) -> str:
    meaningful = [path for path in paths if path != "/normalized_id"]
    if meaningful and all(path.startswith("/description") for path in meaningful):
        return "documentation_only"
    if any(
        path.startswith("/args_schema")
        or path.startswith("/output_schema")
        or path.startswith("/constraints")
        for path in meaningful
    ):
        return "contract_changed"
    return "metadata_or_behavior_changed"


def _risk(paths: Sequence[str]) -> str:
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


__all__ = [
    "SNAPSHOT_SCHEMA",
    "_validate_snapshot",
    "build_snapshot",
    "diff_snapshots",
    "inspect_snapshot",
    "load_snapshot",
]

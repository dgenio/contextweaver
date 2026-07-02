"""Archive-entry helpers for incident packs."""

from __future__ import annotations

import hashlib
import json
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

import yaml

from contextweaver.diagnostics import utc_timestamp
from contextweaver.secrets import DEFAULT_SECRET_MASK, scrub_secrets

DEFAULT_MAX_FILE_BYTES = 64 * 1024
TRUNCATION_MARKER = "\n[contextweaver: truncated]\n"
_SENSITIVE_KEY_MARKERS = (
    "api_key",
    "apikey",
    "auth",
    "bearer",
    "client_secret",
    "credential",
    "password",
    "passwd",
    "private_key",
    "secret",
    "token",
)


def add_json(
    archive: zipfile.ZipFile,
    files: list[dict[str, object]],
    path_in_zip: str,
    payload: object,
    kind: str,
) -> None:
    """Add a JSON payload after key-aware redaction."""
    data = json.dumps(redact_jsonlike(payload), indent=2, sort_keys=True).encode("utf-8")
    add_bytes(archive, files, path_in_zip, data, kind, redacted=True)


def add_text(
    archive: zipfile.ZipFile,
    files: list[dict[str, object]],
    path_in_zip: str,
    text: str,
    kind: str,
) -> None:
    """Add a scrubbed text payload."""
    add_bytes(archive, files, path_in_zip, scrub_secrets(text).encode("utf-8"), kind, redacted=True)


def add_redacted_source(
    archive: zipfile.ZipFile,
    files: list[dict[str, object]],
    path_in_zip: str,
    source: Path,
    kind: str,
    warnings: list[str],
    max_file_bytes: int,
    *,
    structured: str | None = None,
) -> None:
    """Add a source file after scrubbing and per-file truncation."""
    try:
        raw = source.read_bytes()
    except OSError as exc:
        warnings.append(f"cannot include {source}: {exc}")
        return

    sample = raw[: max_file_bytes + 8192]
    text = sample.decode("utf-8", errors="replace")
    text = _redacted_structured_text(source, text, structured)
    encoded = text.encode("utf-8")
    # Truncation is computed from the emitted (redacted) bytes, not just the raw
    # size: redaction/pretty-print expansion (e.g. JSON indenting) can push the
    # entry past the cap even when the raw file fits, and slicing here would
    # otherwise drop content while the manifest still claimed truncated=False.
    truncated = len(raw) > max_file_bytes or len(encoded) > max_file_bytes
    data = encoded[:max_file_bytes]
    if truncated:
        data += TRUNCATION_MARKER.encode("utf-8")
    add_bytes(
        archive,
        files,
        path_in_zip,
        data,
        kind,
        source=source,
        redacted=True,
        truncated=truncated,
    )


def add_bytes(
    archive: zipfile.ZipFile,
    files: list[dict[str, object]],
    path_in_zip: str,
    data: bytes,
    kind: str,
    *,
    source: Path | None = None,
    redacted: bool = False,
    truncated: bool = False,
) -> None:
    """Write bytes and append the matching manifest entry."""
    archive.writestr(path_in_zip, data)
    entry: dict[str, object] = {
        "path": path_in_zip,
        "kind": kind,
        "created_at": utc_timestamp(),
        "size_bytes": len(data),
        "sha256": hashlib.sha256(data).hexdigest(),
        "redacted": redacted,
        "truncated": truncated,
    }
    if source is not None:
        entry["source"] = source_summary(source)
    files.append(entry)


def read_structured_file(path: Path, warnings: list[str]) -> object | None:
    """Best-effort JSON/YAML parse used for summaries."""
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        warnings.append(f"cannot read {path}: {exc}")
        return None
    try:
        if path.suffix.lower() in (".yaml", ".yml"):
            return cast(object, yaml.safe_load(text))
        if path.suffix.lower() == ".json":
            return cast(object, json.loads(text))
    except (json.JSONDecodeError, yaml.YAMLError) as exc:
        warnings.append(f"cannot parse {path}: {exc}")
    return None


def path_from_mapping(data: object | None, key: str, base: Path) -> Path | None:
    """Resolve a path-valued mapping key relative to *base*."""
    if not isinstance(data, dict) or key not in data:
        return None
    path = Path(str(data[key])).expanduser()
    return path if path.is_absolute() else base / path


def source_summary(path: Path, *, exists_only: bool = False) -> dict[str, object]:
    """Return metadata for a user-provided source path."""
    summary: dict[str, object] = {"provided": True, "path": scrub_secrets(str(path))}
    try:
        stat = path.stat()
    except OSError:
        summary["exists"] = False
        return summary
    summary.update(
        {
            "exists": True,
            "size_bytes": stat.st_size,
            "mtime": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        }
    )
    if not exists_only:
        summary["suffix"] = path.suffix.lower()
    return summary


def catalog_counts(data: object | None) -> dict[str, object]:
    """Summarize native or MCP-shaped catalog documents without payload dumps."""
    if isinstance(data, list):
        return {"shape": "list", "tool_count": len(data)}
    if isinstance(data, dict):
        return {
            "shape": "mapping",
            "top_level_keys": sorted(str(key) for key in data),
            "tool_count": _len_if_list(data.get("tools")),
            "resource_count": _len_if_list(data.get("resources")),
            "prompt_count": _len_if_list(data.get("prompts")),
        }
    return {"shape": "unknown"}


def config_summary_values(data: dict[object, object]) -> dict[str, object]:
    """Return redacted values for serve-config keys useful in triage."""
    allowed = {
        "beam_width",
        "cache",
        "cache_stable",
        "catalog",
        "diagnostics",
        "host",
        "mode",
        "name",
        "port",
        "quiet",
        "rate_limits",
        "retry",
        "state_dir",
        "top_k",
        "transport",
        "version",
    }
    redacted = redact_jsonlike(
        {str(key): data[key] for key in sorted(data, key=str) if str(key) in allowed}
    )
    return cast(dict[str, object], redacted)


def redact_jsonlike(value: object, key: str = "") -> object:
    """Redact key-named secrets recursively."""
    if _is_sensitive_key(key):
        return DEFAULT_SECRET_MASK
    if isinstance(value, dict):
        return {str(k): redact_jsonlike(v, str(k)) for k, v in value.items()}
    if isinstance(value, list):
        return [redact_jsonlike(item, key) for item in value]
    if isinstance(value, str):
        return scrub_secrets(value, mask=DEFAULT_SECRET_MASK)
    if value is None or isinstance(value, (bool, int, float)):
        return value
    return scrub_secrets(str(value), mask=DEFAULT_SECRET_MASK)


def _redacted_structured_text(source: Path, text: str, structured: str | None) -> str:
    if structured == "document":
        parsed = _parse_document(source, text)
        if parsed is not None:
            return json.dumps(redact_jsonlike(parsed), indent=2, sort_keys=True)
    if structured == "jsonl":
        return "\n".join(_redact_json_line(line) for line in text.splitlines()) + "\n"
    return scrub_secrets(text, mask=DEFAULT_SECRET_MASK)


def _parse_document(source: Path, text: str) -> object | None:
    try:
        if source.suffix.lower() in (".yaml", ".yml"):
            return cast(object, yaml.safe_load(text))
        if source.suffix.lower() == ".json":
            return cast(object, json.loads(text))
    except (json.JSONDecodeError, yaml.YAMLError):
        return None
    return None


def _redact_json_line(line: str) -> str:
    if not line.strip():
        return line
    try:
        parsed = json.loads(line)
    except json.JSONDecodeError:
        return scrub_secrets(line, mask=DEFAULT_SECRET_MASK)
    return json.dumps(redact_jsonlike(parsed), sort_keys=True, separators=(",", ":"))


def _len_if_list(value: object) -> int:
    return len(value) if isinstance(value, list) else 0


def _is_sensitive_key(key: str) -> bool:
    normalized = key.lower().replace("-", "_")
    return any(marker in normalized for marker in _SENSITIVE_KEY_MARKERS)

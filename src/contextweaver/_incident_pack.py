"""Offline incident-pack builder for ``contextweaver mcp incident-pack``."""

from __future__ import annotations

import json
import platform
import sys
import zipfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from contextweaver._incident_pack_files import (
    DEFAULT_MAX_FILE_BYTES,
    add_json,
    add_redacted_source,
    add_text,
    catalog_counts,
    config_summary_values,
    path_from_mapping,
    read_structured_file,
    source_summary,
)
from contextweaver._version import __version__
from contextweaver.diagnostics import (
    load_diagnostic_events,
    render_diagnostic_report,
    summarize_diagnostics,
    utc_timestamp,
)
from contextweaver.exceptions import ConfigError
from contextweaver.secrets import scrub_secrets

INCIDENT_PACK_VERSION = 1


@dataclass(frozen=True)
class IncidentPackResult:
    """Summary returned after writing an incident pack."""

    path: Path
    manifest: dict[str, object]


def default_incident_pack_path(directory: Path | None = None) -> Path:
    """Return a timestamped default incident-pack path."""
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    base = Path.cwd() if directory is None else directory
    return base / f"contextweaver-incident-pack-{stamp}.zip"


def build_incident_pack(
    output: Path,
    *,
    config: Path | None = None,
    catalog: Path | None = None,
    diagnostics: Path | None = None,
    command_log: Path | None = None,
    max_file_bytes: int = DEFAULT_MAX_FILE_BYTES,
) -> IncidentPackResult:
    """Create a redacted, bounded incident triage archive."""
    if max_file_bytes < 1024:
        raise ConfigError("max_file_bytes must be at least 1024")
    output = output.expanduser()
    if output.exists():
        raise ConfigError(f"incident pack output already exists: {output}")
    output.parent.mkdir(parents=True, exist_ok=True)

    warnings: list[str] = []
    config_data: object | None = None
    config = config.expanduser() if config is not None else None
    if config is not None:
        config_data = read_structured_file(config, warnings)
        if catalog is None:
            catalog = path_from_mapping(config_data, "catalog", config.parent)
        if diagnostics is None:
            diagnostics = path_from_mapping(config_data, "diagnostics", config.parent)
    catalog = catalog.expanduser() if catalog is not None else None
    diagnostics = diagnostics.expanduser() if diagnostics is not None else None
    command_log = command_log.expanduser() if command_log is not None else None

    manifest = _base_manifest(warnings, max_file_bytes)
    try:
        with zipfile.ZipFile(output, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            files = cast(list[dict[str, object]], manifest["files"])
            add_json(
                archive, files, "environment/summary.json", _environment_summary(), "environment"
            )
            _add_config(archive, files, config, config_data, warnings, max_file_bytes)
            _add_catalog(archive, files, catalog, warnings, max_file_bytes)
            _add_diagnostics(archive, files, diagnostics, warnings, max_file_bytes)
            _add_commands(
                archive, files, command_log, config, catalog, diagnostics, warnings, max_file_bytes
            )
            manifest["inputs"] = _input_summary(
                config=config,
                catalog=catalog,
                diagnostics=diagnostics,
                command_log=command_log,
            )
            archive.writestr(
                "manifest.json",
                json.dumps(manifest, indent=2, sort_keys=True).encode("utf-8"),
            )
    except Exception:
        output.unlink(missing_ok=True)
        raise
    return IncidentPackResult(path=output, manifest=manifest)


def _base_manifest(warnings: list[str], max_file_bytes: int) -> dict[str, object]:
    return {
        "version": INCIDENT_PACK_VERSION,
        "created_at": utc_timestamp(),
        "generator": {
            "name": "contextweaver",
            "version": __version__,
            "command": "contextweaver mcp incident-pack",
        },
        "limits": {
            "max_file_bytes": max_file_bytes,
            "redaction": "key-based structured redaction + contextweaver.secrets.scrub_secrets",
        },
        "inputs": {},
        "warnings": warnings,
        "files": [],
    }


def _environment_summary() -> dict[str, object]:
    return {
        "contextweaver_version": __version__,
        "python": {
            "version": platform.python_version(),
            "implementation": platform.python_implementation(),
            "executable": scrub_secrets(sys.executable),
        },
        "platform": {
            "system": platform.system(),
            "release": platform.release(),
            "machine": platform.machine(),
        },
        "working_directory": scrub_secrets(str(Path.cwd())),
    }


def _add_config(
    archive: zipfile.ZipFile,
    files: list[dict[str, object]],
    config: Path | None,
    data: object | None,
    warnings: list[str],
    max_file_bytes: int,
) -> None:
    summary: dict[str, object] = {"provided": config is not None}
    if config is not None:
        summary.update(source_summary(config))
        if isinstance(data, dict):
            summary["keys"] = sorted(str(key) for key in data)
            summary["values"] = config_summary_values(data)
    add_json(archive, files, "config/config_summary.json", summary, "config_summary")
    if config is not None:
        add_redacted_source(
            archive,
            files,
            "config/redacted_config.txt",
            config,
            "config",
            warnings,
            max_file_bytes,
            structured="document",
        )


def _add_catalog(
    archive: zipfile.ZipFile,
    files: list[dict[str, object]],
    catalog: Path | None,
    warnings: list[str],
    max_file_bytes: int,
) -> None:
    if catalog is None:
        return
    data = read_structured_file(catalog, warnings)
    summary = source_summary(catalog)
    summary.update(catalog_counts(data))
    add_json(archive, files, "catalog/catalog_summary.json", summary, "catalog_summary")
    add_redacted_source(
        archive,
        files,
        "catalog/redacted_catalog.txt",
        catalog,
        "catalog",
        warnings,
        max_file_bytes,
        structured="document",
    )


def _add_diagnostics(
    archive: zipfile.ZipFile,
    files: list[dict[str, object]],
    diagnostics: Path | None,
    warnings: list[str],
    max_file_bytes: int,
) -> None:
    if diagnostics is None:
        return
    try:
        summary = summarize_diagnostics(load_diagnostic_events(diagnostics))
    except ConfigError as exc:
        warnings.append(str(exc))
    else:
        add_json(archive, files, "diagnostics/summary.json", summary, "diagnostics_summary")
        add_text(
            archive, files, "diagnostics/report.md", render_diagnostic_report(summary), "report"
        )
    add_redacted_source(
        archive,
        files,
        "diagnostics/redacted_events.jsonl",
        diagnostics,
        "diagnostics",
        warnings,
        max_file_bytes,
        structured="jsonl",
    )


def _add_commands(
    archive: zipfile.ZipFile,
    files: list[dict[str, object]],
    command_log: Path | None,
    config: Path | None,
    catalog: Path | None,
    diagnostics: Path | None,
    warnings: list[str],
    max_file_bytes: int,
) -> None:
    add_text(
        archive,
        files,
        "commands/repro_checklist.md",
        _repro_checklist(config=config, catalog=catalog, diagnostics=diagnostics),
        "repro_checklist",
    )
    if command_log is not None:
        add_redacted_source(
            archive,
            files,
            "commands/command_log_redacted.txt",
            command_log,
            "command_log",
            warnings,
            max_file_bytes,
        )


def _repro_checklist(config: Path | None, catalog: Path | None, diagnostics: Path | None) -> str:
    lines = [
        "# Reproduction Checklist",
        "",
        "- [ ] Record the contextweaver version and installation method.",
        "- [ ] Record the host client, OS, and Python version.",
        "- [ ] Run the gateway dry-run with the same configuration.",
    ]
    if config is not None:
        lines.append(f"  - `contextweaver mcp serve --config {config} --dry-run`")
    elif catalog is not None:
        lines.append(f"  - `contextweaver mcp serve --catalog {catalog} --dry-run`")
    if catalog is not None:
        lines.append(f"- [ ] Inspect the catalog: `contextweaver mcp inspect --catalog {catalog}`")
    if diagnostics is not None:
        lines.append(
            f"- [ ] Aggregate diagnostics: `contextweaver mcp stats --events {diagnostics}`"
        )
    lines.extend(
        [
            "- [ ] Attach the command log only when it was captured explicitly for this incident.",
            "- [ ] Note the observed behavior, expected behavior, and minimal failing prompt.",
            "",
        ]
    )
    return "\n".join(lines)


def _input_summary(**paths: Path | None) -> dict[str, object]:
    return {
        name: source_summary(path, exists_only=True) if path is not None else {"provided": False}
        for name, path in paths.items()
    }

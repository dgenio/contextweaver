"""Tests for ``contextweaver mcp incident-pack`` (#661)."""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Any

from contextweaver._incident_pack import build_incident_pack


def _run(*args: str, cwd: str | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, "-m", "contextweaver", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=cwd,
        env=env,
    )


def _manifest(path: Path) -> dict[str, Any]:
    with zipfile.ZipFile(path) as archive:
        return json.loads(archive.read("manifest.json"))


def _zip_text(path: Path) -> str:
    with zipfile.ZipFile(path) as archive:
        chunks = [
            archive.read(name).decode("utf-8", errors="replace") for name in archive.namelist()
        ]
    return "\n".join(chunks)


def test_incident_pack_includes_manifest_and_redacts_sources(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        json.dumps(
            {
                "tools": [
                    {
                        "name": "demo.search",
                        "description": "Demo search",
                        "inputSchema": {"type": "object"},
                        "apiToken": "lime",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    diagnostics = tmp_path / "events.jsonl"
    diagnostics.write_text(
        json.dumps(
            {
                "event": "execute.completed",
                "timestamp": "2026-06-11T00:00:00+00:00",
                "session_id": "s1",
                "success": True,
                "duration_ms": 12,
                "attributes": {"raw_tokens": 20, "compact_tokens": 5, "secret": "plum"},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    config = tmp_path / "gateway.yaml"
    config.write_text(
        "catalog: catalog.json\ndiagnostics: events.jsonl\nmode: gateway\npassword: blue\n",
        encoding="utf-8",
    )
    command_log = tmp_path / "commands.txt"
    command_log.write_text("contextweaver mcp serve token=commandsecret\n", encoding="utf-8")
    output = tmp_path / "incident.zip"

    result = build_incident_pack(
        output,
        config=config,
        command_log=command_log,
        max_file_bytes=4096,
    )

    assert result.path == output
    manifest = _manifest(output)
    names = {entry["path"] for entry in manifest["files"]}
    assert names >= {
        "environment/summary.json",
        "config/config_summary.json",
        "config/redacted_config.txt",
        "catalog/catalog_summary.json",
        "catalog/redacted_catalog.txt",
        "diagnostics/summary.json",
        "diagnostics/redacted_events.jsonl",
        "commands/repro_checklist.md",
        "commands/command_log_redacted.txt",
    }
    for entry in manifest["files"]:
        with zipfile.ZipFile(output) as archive:
            data = archive.read(entry["path"])
        assert entry["sha256"] == hashlib.sha256(data).hexdigest()
        assert entry["created_at"]
        assert entry["size_bytes"] == len(data)

    text = _zip_text(output)
    assert "blue" not in text
    assert "lime" not in text
    assert "plum" not in text
    assert "commandsecret" not in text
    assert "[REDACTED-SECRET]" in text
    assert manifest["inputs"]["command_log"]["provided"] is True
    assert manifest["warnings"] == []


def test_incident_pack_omits_command_log_unless_explicit(tmp_path: Path) -> None:
    output = tmp_path / "incident.zip"

    build_incident_pack(output)

    manifest = _manifest(output)
    names = {entry["path"] for entry in manifest["files"]}
    assert "commands/repro_checklist.md" in names
    assert "commands/command_log_redacted.txt" not in names
    assert manifest["inputs"]["command_log"] == {"provided": False}


def test_incident_pack_marks_truncated_sources(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    catalog.write_text("{" + '"payload":"' + ("x" * 5000) + '"}', encoding="utf-8")
    output = tmp_path / "incident.zip"

    build_incident_pack(output, catalog=catalog, max_file_bytes=1024)

    manifest = _manifest(output)
    entry = next(
        item for item in manifest["files"] if item["path"] == "catalog/redacted_catalog.txt"
    )
    assert entry["truncated"] is True
    with zipfile.ZipFile(output) as archive:
        text = archive.read("catalog/redacted_catalog.txt").decode("utf-8")
    assert text.endswith("[contextweaver: truncated]\n")
    assert entry["size_bytes"] <= 1024 + len("\n[contextweaver: truncated]\n")


def test_incident_pack_marks_redaction_expanded_sources(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    payload = {f"k{index}": index for index in range(100)}
    raw = json.dumps(payload, separators=(",", ":"))
    catalog.write_text(raw, encoding="utf-8")
    # Precondition: the raw file fits under the cap; only pretty-print/redaction
    # expansion pushes the emitted entry past it, so this exercises the
    # emitted-bytes truncation path rather than raw-size truncation.
    assert len(raw.encode("utf-8")) <= 1024
    output = tmp_path / "incident.zip"

    build_incident_pack(output, catalog=catalog, max_file_bytes=1024)

    manifest = _manifest(output)
    entry = next(
        item for item in manifest["files"] if item["path"] == "catalog/redacted_catalog.txt"
    )
    assert entry["truncated"] is True
    with zipfile.ZipFile(output) as archive:
        text = archive.read("catalog/redacted_catalog.txt").decode("utf-8")
    assert text.endswith("[contextweaver: truncated]\n")
    assert entry["size_bytes"] <= 1024 + len("\n[contextweaver: truncated]\n")


def test_incident_pack_redacts_oversized_structured_document(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    # A structured document larger than the decode window (max_file_bytes +
    # 8192) whose value under a sensitive key is NOT secret-pattern-shaped.
    # Key-based redaction requires parsing a complete document, so a truncated
    # fragment would fail to parse and downgrade to pattern-only scrubbing,
    # leaking the value. Full-document redaction must mask it regardless of size.
    payload = {"api_key": "plaintextcredential", "zzz_pad": ["x" * 200 for _ in range(80)]}
    raw = json.dumps(payload)  # insertion order keeps api_key in the first bytes
    catalog.write_text(raw, encoding="utf-8")
    assert len(raw.encode("utf-8")) > 1024 + 8192
    output = tmp_path / "incident.zip"

    build_incident_pack(output, catalog=catalog, max_file_bytes=1024)

    manifest = _manifest(output)
    entry = next(
        item for item in manifest["files"] if item["path"] == "catalog/redacted_catalog.txt"
    )
    assert entry["truncated"] is True
    with zipfile.ZipFile(output) as archive:
        text = archive.read("catalog/redacted_catalog.txt").decode("utf-8")
    assert "plaintextcredential" not in text
    assert "[REDACTED-SECRET]" in text


def test_incident_pack_cli_creates_zip_from_config(tmp_path: Path) -> None:
    catalog = tmp_path / "catalog.json"
    catalog.write_text(
        '[{"name":"demo","description":"Demo","inputSchema":{"type":"object"}}]', encoding="utf-8"
    )
    config = tmp_path / "gateway.yaml"
    config.write_text("catalog: catalog.json\nmode: gateway\n", encoding="utf-8")
    output = tmp_path / "incident.zip"

    result = _run("mcp", "incident-pack", "--config", str(config), "--out", str(output))

    assert result.returncode == 0, result.stderr
    assert output.exists()
    assert str(output) in result.stdout
    manifest = _manifest(output)
    assert manifest["version"] == 1
    assert manifest["inputs"]["config"]["provided"] is True
    assert manifest["inputs"]["catalog"]["provided"] is True


def test_incident_pack_cli_reports_error_without_param_hint(tmp_path: Path) -> None:
    existing = tmp_path / "incident.zip"
    existing.write_text("occupied", encoding="utf-8")

    result = _run("mcp", "incident-pack", "--out", str(existing))

    assert result.returncode != 0
    assert "already exists" in result.stderr
    # The failure originates from an existing --out target here, but errors can
    # also come from --config/--catalog/--diagnostics/--max-file-bytes, so the
    # CLI must not misattribute every failure to a single "--out" param hint.
    assert "for '--out'" not in result.stderr


def test_mcp_help_lists_incident_pack_subcommand() -> None:
    result = _run("mcp", "--help")

    assert result.returncode == 0
    assert "incident-pack" in result.stdout

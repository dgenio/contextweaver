"""Tests for the ``contextweaver mcp serve`` CLI sub-app (issues #243, #246).

The actual stdio server cannot be exercised in a unit test (it blocks on
stdin/stdout), so these tests cover the surfaces a downstream user touches
first: ``--help`` output, catalog validation, ``--dry-run`` semantics, and
the flag-parsing rules (mutual exclusion of ``--gateway``/``--proxy``).
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest
import typer

from contextweaver._mcp_cli import (
    _build_dispatch_controls,
    _build_primitive_runtime,
    _build_runtime,
    _load_primitive_defs_from_catalog,
    _load_serve_config,
    _load_tool_defs_from_catalog,
    _ServeMode,
)
from contextweaver.adapters.proxy_runtime import ExposureMode
from contextweaver.data import gateway_catalog_path

# Rich renders Typer ``--help`` with per-character ANSI styling (each dash and
# each token in a flag name gets wrapped in its own ``\x1b[...m`` sequence),
# so the literal substring ``"--catalog"`` does not appear verbatim in the
# rendered stdout when the CI runner exports ``TERM`` / Rich auto-detects a
# colour-capable sink. Strip the SGR escapes before asserting on flag names
# so the test is portable across "plain" local subprocess runs and "coloured"
# CI runs.
_ANSI_SGR_RE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    """Return *text* with all ANSI SGR (colour / style) sequences removed."""
    return _ANSI_SGR_RE.sub("", text)


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


# ------------------------------------------------------------------
# Help output (subcommand discovery)
# ------------------------------------------------------------------


def test_mcp_subapp_listed_in_root_help() -> None:
    """``contextweaver --help`` advertises the new ``mcp`` sub-app."""
    result = _run("--help")
    assert result.returncode == 0
    assert "mcp" in _strip_ansi(result.stdout)


def test_mcp_help_lists_serve_subcommand() -> None:
    """``contextweaver mcp --help`` shows the ``serve`` subcommand."""
    result = _run("mcp", "--help")
    assert result.returncode == 0
    out = _strip_ansi(result.stdout)
    assert "serve" in out
    assert "MCP server entrypoints" in out or "MCP" in out
    assert "inspect" in out
    assert "stats" in out


def test_mcp_serve_help_shows_required_catalog_flag() -> None:
    """``serve --help`` advertises the required ``--catalog`` option and modes."""
    result = _run("mcp", "serve", "--help")
    assert result.returncode == 0
    out = _strip_ansi(result.stdout)
    assert "--catalog" in out
    assert "--config" in out
    assert "--mode" in out
    assert "--gateway" in out
    assert "--proxy" in out
    assert "--dry-run" in out
    assert "--diagnostics" in out
    assert "--quiet" in out


# ------------------------------------------------------------------
# Dry-run validation against the packaged catalog
# ------------------------------------------------------------------


def test_serve_dry_run_with_packaged_gateway_catalog() -> None:
    """``--dry-run`` exits 0 and prints the catalog summary on stderr."""
    catalog = gateway_catalog_path()
    assert catalog.exists(), f"packaged catalog missing: {catalog}"
    result = _run(
        "mcp",
        "serve",
        "--catalog",
        str(catalog),
        "--mode",
        "gateway",
        "--dry-run",
    )
    assert result.returncode == 0
    combined = result.stderr + result.stdout
    assert "mode=gateway" in combined
    assert "tools=60" in combined
    assert "dry-run" in combined


def test_serve_dry_run_advertises_installed_package_version() -> None:
    """``serve`` advertises the installed contextweaver version by default."""
    import contextweaver

    catalog = gateway_catalog_path()
    result = _run("mcp", "serve", "--catalog", str(catalog), "--dry-run")
    assert result.returncode == 0
    combined = result.stderr + result.stdout
    assert f"version={contextweaver.__version__}" in combined


def test_serve_dry_run_explicit_version_overrides_default() -> None:
    """An explicit ``--version`` wins over the package-version default."""
    catalog = gateway_catalog_path()
    result = _run("mcp", "serve", "--catalog", str(catalog), "--version", "9.9.9-test", "--dry-run")
    assert result.returncode == 0
    combined = result.stderr + result.stdout
    assert "version=9.9.9-test" in combined


def test_serve_dry_run_proxy_mode_with_packaged_catalog() -> None:
    """Proxy mode also validates the catalog and exits 0."""
    catalog = gateway_catalog_path()
    result = _run(
        "mcp",
        "serve",
        "--catalog",
        str(catalog),
        "--mode",
        "proxy",
        "--dry-run",
    )
    assert result.returncode == 0
    assert "mode=proxy" in result.stderr + result.stdout


def test_serve_dry_run_writes_catalog_diagnostic_event(tmp_path: Path) -> None:
    events = tmp_path / "gateway.jsonl"
    result = _run(
        "mcp",
        "serve",
        "--catalog",
        str(gateway_catalog_path()),
        "--diagnostics",
        str(events),
        "--dry-run",
        "--quiet",
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert result.stderr == ""
    payload = json.loads(events.read_text(encoding="utf-8"))
    assert payload["event"] == "catalog.loaded"
    assert payload["attributes"]["tool_count"] == 60


def test_serve_gateway_flag_shortcut() -> None:
    """``--gateway`` flag overrides ``--mode`` (matches the issue body's UX)."""
    catalog = gateway_catalog_path()
    result = _run(
        "mcp",
        "serve",
        "--catalog",
        str(catalog),
        "--gateway",
        "--dry-run",
    )
    assert result.returncode == 0
    assert "mode=gateway" in result.stderr + result.stdout


def test_serve_proxy_flag_shortcut() -> None:
    """``--proxy`` flag overrides ``--mode`` (matches the issue body's UX)."""
    catalog = gateway_catalog_path()
    result = _run(
        "mcp",
        "serve",
        "--catalog",
        str(catalog),
        "--proxy",
        "--dry-run",
    )
    assert result.returncode == 0
    assert "mode=proxy" in result.stderr + result.stdout


def test_serve_rejects_both_gateway_and_proxy_flags() -> None:
    """``--gateway`` and ``--proxy`` together is a usage error (exit 2)."""
    catalog = gateway_catalog_path()
    result = _run(
        "mcp",
        "serve",
        "--catalog",
        str(catalog),
        "--gateway",
        "--proxy",
        "--dry-run",
    )
    assert result.returncode != 0
    combined = (result.stderr + result.stdout).lower()
    assert "mutually exclusive" in combined or "gateway" in combined


def test_serve_missing_catalog_is_usage_error(tmp_path: Path) -> None:
    """Pointing ``--catalog`` at a non-existent file is rejected cleanly."""
    missing = tmp_path / "nope.yaml"
    result = _run(
        "mcp",
        "serve",
        "--catalog",
        str(missing),
        "--dry-run",
    )
    assert result.returncode != 0
    assert "catalog file not found" in (result.stderr + result.stdout)


def test_serve_rejects_invalid_yaml(tmp_path: Path) -> None:
    """Malformed YAML produces a usage error, not a stack trace."""
    bad = tmp_path / "bad.yaml"
    bad.write_text("not: a: list:\n- :\n  -", encoding="utf-8")
    result = _run("mcp", "serve", "--catalog", str(bad), "--dry-run")
    assert result.returncode != 0


def test_serve_rejects_empty_catalog(tmp_path: Path) -> None:
    """An empty catalog file is rejected as a usage error."""
    empty = tmp_path / "empty.yaml"
    empty.write_text("[]\n", encoding="utf-8")
    result = _run("mcp", "serve", "--catalog", str(empty), "--dry-run")
    assert result.returncode != 0


# ------------------------------------------------------------------
# Config-file launch (issue #346 — zero-Python drop-in)
# ------------------------------------------------------------------


def test_serve_config_file_drives_dry_run(tmp_path: Path) -> None:
    """A single ``--config`` file supplies catalog + options for a dry run."""
    config = tmp_path / "gateway.yaml"
    config.write_text(
        f"catalog: {gateway_catalog_path()}\nmode: proxy\ntop_k: 7\nbeam_width: 2\n",
        encoding="utf-8",
    )
    result = _run("mcp", "serve", "--config", str(config), "--dry-run")
    assert result.returncode == 0, result.stderr
    combined = result.stderr + result.stdout
    assert "mode=proxy" in combined
    assert "top_k=7" in combined
    assert "beam_width=2" in combined
    assert "tools=60" in combined


def test_serve_config_resolves_catalog_relative_to_config_file(tmp_path: Path) -> None:
    """A config remains portable when the server starts from another directory."""
    config_dir = tmp_path / "gateway"
    config_dir.mkdir()
    catalog = config_dir / "catalog.json"
    catalog.write_text(
        '[{"name": "demo", "description": "Demo tool", "inputSchema": {"type": "object"}}]',
        encoding="utf-8",
    )
    config = config_dir / "gateway.yaml"
    config.write_text("catalog: catalog.json\n", encoding="utf-8")

    result = _run("mcp", "serve", "--config", str(config), "--dry-run")

    assert result.returncode == 0, result.stderr
    assert "tools=1" in (result.stderr + result.stdout)


def test_serve_cli_flag_overrides_config(tmp_path: Path) -> None:
    """Explicit CLI flags win over config-file values."""
    config = tmp_path / "gateway.yaml"
    config.write_text(
        f"catalog: {gateway_catalog_path()}\nmode: proxy\ntop_k: 7\n",
        encoding="utf-8",
    )
    result = _run("mcp", "serve", "--config", str(config), "--top-k", "3", "--gateway", "--dry-run")
    assert result.returncode == 0, result.stderr
    combined = result.stderr + result.stdout
    assert "top_k=3" in combined
    assert "mode=gateway" in combined


def test_serve_without_catalog_or_config_is_usage_error() -> None:
    """Omitting both ``--catalog`` and ``--config`` is a clean usage error."""
    result = _run("mcp", "serve", "--dry-run")
    assert result.returncode != 0
    assert "catalog" in (result.stderr + result.stdout).lower()


def test_load_serve_config_rejects_unknown_key(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text("catalog: x.json\nbogus: 1\n", encoding="utf-8")
    with pytest.raises(Exception) as exc_info:
        _load_serve_config(cfg)
    assert "unknown config key" in str(exc_info.value).lower()


def test_load_serve_config_requires_catalog(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text("top_k: 5\n", encoding="utf-8")
    with pytest.raises(Exception) as exc_info:
        _load_serve_config(cfg)
    assert "catalog" in str(exc_info.value).lower()


def test_load_serve_config_valid_roundtrip(tmp_path: Path) -> None:
    cfg = tmp_path / "c.json"
    cfg.write_text('{"catalog": "x.json", "top_k": 9, "mode": "gateway"}', encoding="utf-8")
    loaded = _load_serve_config(cfg)
    assert loaded["catalog"] == str((tmp_path / "x.json").resolve())
    assert loaded["top_k"] == 9
    assert loaded["mode"] == "gateway"


def test_load_serve_config_coerces_quoted_bool(tmp_path: Path) -> None:
    # A quoted "false" must parse to False, not bool("false") == True.
    cfg = tmp_path / "c.yaml"
    cfg.write_text('catalog: x.json\ncache_stable: "false"\n', encoding="utf-8")
    loaded = _load_serve_config(cfg)
    assert loaded["cache_stable"] is False

    cfg.write_text('catalog: x.json\ncache_stable: "on"\n', encoding="utf-8")
    assert _load_serve_config(cfg)["cache_stable"] is True


def test_load_serve_config_resolves_diagnostics_and_quiet(tmp_path: Path) -> None:
    cfg = tmp_path / "gateway.yaml"
    cfg.write_text(
        "catalog: catalog.json\ndiagnostics: logs/events.jsonl\nquiet: true\n",
        encoding="utf-8",
    )
    loaded = _load_serve_config(cfg)
    assert loaded["diagnostics"] == str((tmp_path / "logs" / "events.jsonl").resolve())
    assert loaded["quiet"] is True


def test_load_serve_config_rejects_bad_types(tmp_path: Path) -> None:
    cfg = tmp_path / "c.yaml"
    cfg.write_text("catalog: x.json\ntop_k: not-a-number\n", encoding="utf-8")
    with pytest.raises(Exception) as exc_info:
        _load_serve_config(cfg)
    assert "top_k must be an integer" in str(exc_info.value)

    cfg.write_text("catalog: x.json\nmode: bogus\n", encoding="utf-8")
    with pytest.raises(Exception) as exc_info:
        _load_serve_config(cfg)
    assert "mode must be" in str(exc_info.value)

    cfg.write_text("catalog: x.json\ncache_stable: maybe\n", encoding="utf-8")
    with pytest.raises(Exception) as exc_info:
        _load_serve_config(cfg)
    assert "cache_stable must be a boolean" in str(exc_info.value)


# ------------------------------------------------------------------
# Catalog loader unit tests (programmatic, no subprocess)
# ------------------------------------------------------------------


def test_load_tool_defs_from_packaged_catalog_returns_60_entries() -> None:
    """The packaged gateway catalog ships 60 tools."""
    defs = _load_tool_defs_from_catalog(gateway_catalog_path())
    assert len(defs) == 60
    # Every entry must carry the MCP-required keys after conversion.
    for entry in defs:
        assert "name" in entry
        assert "description" in entry
        assert "inputSchema" in entry


def test_load_tool_defs_accepts_mcp_shape(tmp_path: Path) -> None:
    """A raw MCP ``tools/list`` snapshot loads without conversion."""
    snapshot = tmp_path / "mcp.json"
    snapshot.write_text(
        '[{"name": "x.y", "description": "z", "inputSchema": {"type": "object"}}]',
        encoding="utf-8",
    )
    defs = _load_tool_defs_from_catalog(snapshot)
    assert len(defs) == 1
    assert defs[0]["name"] == "x.y"
    assert defs[0]["inputSchema"] == {"type": "object"}


def test_load_tool_defs_accepts_snapshot_tools_object(tmp_path: Path) -> None:
    """A real-MCP snapshot object ({"_source": ..., "tools": [...]}) is unwrapped."""
    snapshot = tmp_path / "snap.json"
    snapshot.write_text(
        '{"_source": "demo", "tools": '
        '[{"name": "fs.read", "description": "read", "inputSchema": {"type": "object"}}]}',
        encoding="utf-8",
    )
    defs = _load_tool_defs_from_catalog(snapshot)
    assert len(defs) == 1
    assert defs[0]["name"] == "fs.read"


def test_load_tool_defs_rejects_non_list_root(tmp_path: Path) -> None:
    """The catalog file root must be a sequence."""
    bad = tmp_path / "bad.json"
    bad.write_text('{"items": []}', encoding="utf-8")
    with pytest.raises(Exception) as exc_info:
        _load_tool_defs_from_catalog(bad)
    # Typer's BadParameter inherits from ClickException; the body of the
    # message names the catalog file.
    assert "catalog" in str(exc_info.value).lower()


# ------------------------------------------------------------------
# Runtime construction (programmatic)
# ------------------------------------------------------------------


def test_build_runtime_gateway_mode_registers_all_tools() -> None:
    """``_build_runtime`` populates the runtime catalog from the packaged file."""
    runtime = _build_runtime(
        gateway_catalog_path(),
        mode=_ServeMode.gateway,
        top_k=10,
        beam_width=3,
        cache_stable=False,
    )
    assert runtime.mode == ExposureMode.GATEWAY
    assert len(runtime.list_tool_ids()) == 60


def test_build_runtime_proxy_mode_registers_all_tools() -> None:
    runtime = _build_runtime(
        gateway_catalog_path(),
        mode=_ServeMode.proxy,
        top_k=10,
        beam_width=3,
        cache_stable=False,
    )
    assert runtime.mode == ExposureMode.TRANSPARENT
    assert len(runtime.list_tool_ids()) == 60


def test_mcp_inspect_reports_catalog_savings_json() -> None:
    result = _run(
        "mcp",
        "inspect",
        "--catalog",
        str(gateway_catalog_path()),
        "--format",
        "json",
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["tool_count"] == 60
    assert payload["exposed_tool_count"] == 3
    assert payload["schema_tokens_avoided"] > 0


def test_mcp_stats_aggregates_jsonl(tmp_path: Path) -> None:
    events = tmp_path / "events.jsonl"
    events.write_text(
        json.dumps(
            {
                "event": "execute.completed",
                "timestamp": "2026-06-11T00:00:00+00:00",
                "session_id": "s1",
                "success": True,
                "duration_ms": 12,
                "attributes": {"raw_tokens": 100, "compact_tokens": 25},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    result = _run("mcp", "stats", "--events", str(events), "--format", "json")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["event_count"] == 1
    assert payload["tokens_saved"] == 75


# ---------------------------------------------------------------------------
# Dispatch-path control config (#529 / #482 / #512)
# ---------------------------------------------------------------------------


def test_build_dispatch_controls_builds_each_block() -> None:
    retry, limiter, cache = _build_dispatch_controls(
        {
            "retry": {"max_attempts": 3, "base_delay": 0.2},
            "rate_limits": {"tool_execute": {"max_calls_per_session": 5}},
            "cache": {"read_only": True, "ttl_seconds": 30, "allow": ["files:read@1#0badc0de"]},
        }
    )
    assert retry is not None and retry.max_attempts == 3
    assert limiter is not None
    assert cache is not None
    assert cache.admits("files:read@1#0badc0de") is True
    assert cache.admits("other:tool") is False


def test_build_dispatch_controls_absent_blocks_are_none() -> None:
    assert _build_dispatch_controls({}) == (None, None, None)


def test_build_dispatch_controls_rejects_string_cache_allow() -> None:
    # A bare string would otherwise collapse into a set of single characters.
    with pytest.raises(typer.BadParameter):
        _build_dispatch_controls({"cache": {"read_only": True, "allow": "files:read"}})


def test_build_dispatch_controls_rejects_bad_retryable_codes() -> None:
    with pytest.raises(typer.BadParameter):
        _build_dispatch_controls({"retry": {"retryable_codes": "UPSTREAM_TIMEOUT"}})


# ------------------------------------------------------------------
# Gateway resources/prompts wiring (#669 / #670)
# ------------------------------------------------------------------

_SNAPSHOT_CATALOG = {
    "tools": [{"name": "demo", "description": "Demo tool", "inputSchema": {"type": "object"}}],
    "resources": [
        {"uri": "file:///docs/readme.md", "name": "README", "mimeType": "text/markdown"},
        {"uri": "postgres://db/users", "name": "users", "description": "user records"},
    ],
    "prompts": [
        {"name": "greet", "description": "Greet a user", "arguments": [{"name": "who"}]},
    ],
}


def test_load_primitive_defs_reads_snapshot_keys(tmp_path: Path) -> None:
    catalog = tmp_path / "snapshot.json"
    catalog.write_text(json.dumps(_SNAPSHOT_CATALOG), encoding="utf-8")
    resources, prompts = _load_primitive_defs_from_catalog(catalog)
    assert [r["uri"] for r in resources] == ["file:///docs/readme.md", "postgres://db/users"]
    assert [p["name"] for p in prompts] == ["greet"]


def test_load_primitive_defs_bare_list_yields_empty(tmp_path: Path) -> None:
    """A tools-only (bare list) catalog declares no primitives."""
    catalog = tmp_path / "tools_only.json"
    catalog.write_text(json.dumps(_SNAPSHOT_CATALOG["tools"]), encoding="utf-8")
    assert _load_primitive_defs_from_catalog(catalog) == ([], [])


def test_load_primitive_defs_skips_malformed_entries(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """Non-dict and identity-less entries are dropped with a warning, not silently."""
    catalog = tmp_path / "malformed.json"
    catalog.write_text(
        json.dumps(
            {
                "resources": [
                    {"uri": "file:///ok.md", "name": "ok"},
                    "not-a-dict",
                    {"name": "missing-uri"},
                ],
                "prompts": [
                    {"name": "good"},
                    {"description": "missing-name"},
                ],
            }
        ),
        encoding="utf-8",
    )
    with caplog.at_level(logging.WARNING, logger="contextweaver.mcp_cli"):
        resources, prompts = _load_primitive_defs_from_catalog(catalog)
    assert [r["uri"] for r in resources] == ["file:///ok.md"]
    assert [p["name"] for p in prompts] == ["good"]
    # One warning per dropped entry (2 resources + 1 prompt malformed).
    assert sum("skipping" in rec.message for rec in caplog.records) == 3


def test_build_primitive_runtime_registers_and_shares_context(tmp_path: Path) -> None:
    catalog = tmp_path / "snapshot.json"
    catalog.write_text(json.dumps(_SNAPSHOT_CATALOG), encoding="utf-8")
    tool_runtime = _build_runtime(
        catalog, mode=_ServeMode.gateway, top_k=10, beam_width=3, cache_stable=False
    )
    primitive_runtime = _build_primitive_runtime(catalog, tool_runtime, top_k=10, beam_width=3)
    assert primitive_runtime is not None
    assert len(primitive_runtime.resource_ids()) == 2
    assert len(primitive_runtime.prompt_ids()) == 1
    # The shared ContextManager keeps reads in one artifact / tool_view surface.
    assert primitive_runtime.context_manager is tool_runtime.context_manager


def test_build_primitive_runtime_none_without_primitives(tmp_path: Path) -> None:
    catalog = tmp_path / "tools_only.json"
    catalog.write_text(json.dumps(_SNAPSHOT_CATALOG["tools"]), encoding="utf-8")
    tool_runtime = _build_runtime(
        catalog, mode=_ServeMode.gateway, top_k=10, beam_width=3, cache_stable=False
    )
    assert _build_primitive_runtime(catalog, tool_runtime, top_k=10, beam_width=3) is None


def test_serve_dry_run_reports_primitive_counts(tmp_path: Path) -> None:
    """A snapshot catalog with resources/prompts surfaces their counts in the summary."""
    catalog = tmp_path / "snapshot.json"
    catalog.write_text(json.dumps(_SNAPSHOT_CATALOG), encoding="utf-8")
    result = _run("mcp", "serve", "--catalog", str(catalog), "--gateway", "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "primitives=resources=2 prompts=1" in (result.stderr + result.stdout)


def test_serve_dry_run_primitives_off_for_tools_only(tmp_path: Path) -> None:
    catalog = tmp_path / "tools_only.json"
    catalog.write_text(json.dumps(_SNAPSHOT_CATALOG["tools"]), encoding="utf-8")
    result = _run("mcp", "serve", "--catalog", str(catalog), "--gateway", "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "primitives=off" in (result.stderr + result.stdout)


def test_serve_proxy_mode_does_not_wire_primitives(tmp_path: Path) -> None:
    """Proxy mode is a transparent tool passthrough; primitives stay off."""
    catalog = tmp_path / "snapshot.json"
    catalog.write_text(json.dumps(_SNAPSHOT_CATALOG), encoding="utf-8")
    result = _run("mcp", "serve", "--catalog", str(catalog), "--proxy", "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "primitives=off" in (result.stderr + result.stdout)

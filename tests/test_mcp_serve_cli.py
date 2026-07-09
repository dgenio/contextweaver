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
    _build_dispatch_config,
    _build_primitive_runtime,
    _build_runtime,
    _dispatch_behaviors,
    _find_upstream_startup_error,
    _load_primitive_defs_from_catalog,
    _load_serve_config,
    _load_tool_defs_from_catalog,
    _ServeMode,
)
from contextweaver.adapters.gateway_presets import GATEWAY_PRESET_NAMES, GatewayPreset
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


def _run(
    *args: str, cwd: str | None = None, env_overrides: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    if env_overrides:
        env.update(env_overrides)
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


def test_build_runtime_secure_by_default_enables_redaction() -> None:
    """#744: serving is secure-by-default — redaction + classifier are on."""
    runtime = _build_runtime(
        gateway_catalog_path(),
        mode=_ServeMode.gateway,
        top_k=10,
        beam_width=3,
        cache_stable=False,
    )
    assert runtime._redact_secrets is True
    assert runtime.context_manager._redact_secrets is True
    assert runtime.context_manager._sensitivity_classifier is not None


def test_build_runtime_no_redact_opts_out() -> None:
    runtime = _build_runtime(
        gateway_catalog_path(),
        mode=_ServeMode.gateway,
        top_k=10,
        beam_width=3,
        cache_stable=False,
        secure=False,
    )
    assert runtime._redact_secrets is False
    assert runtime.context_manager._sensitivity_classifier is None


def test_build_runtime_wires_policy() -> None:
    from contextweaver.adapters.gateway_authz import PolicyRule, ToolPolicy

    policy = ToolPolicy(rules=[PolicyRule(action="deny", tool="*delete*")])
    runtime = _build_runtime(
        gateway_catalog_path(),
        mode=_ServeMode.gateway,
        top_k=10,
        beam_width=3,
        cache_stable=False,
        policy=policy,
    )
    assert runtime._policy is policy


def test_load_serve_config_parses_redact_and_policy(tmp_path: Path) -> None:
    cfg = tmp_path / "gateway.yaml"
    cfg.write_text(
        "catalog: cat.json\n"
        "redact: false\n"
        "policy:\n"
        "  default: allow\n"
        "  rules:\n"
        "    - action: deny\n"
        "      tool: '*delete*'\n",
        encoding="utf-8",
    )
    (tmp_path / "cat.json").write_text("[]", encoding="utf-8")
    loaded = _load_serve_config(cfg)
    assert loaded["redact"] is False
    assert loaded["policy"]["rules"][0]["action"] == "deny"


def test_load_serve_config_rejects_non_mapping_policy(tmp_path: Path) -> None:
    cfg = tmp_path / "gateway.yaml"
    cfg.write_text("catalog: cat.json\npolicy: not-a-mapping\n", encoding="utf-8")
    (tmp_path / "cat.json").write_text("[]", encoding="utf-8")
    with pytest.raises(typer.BadParameter):
        _load_serve_config(cfg)


def test_serve_no_redact_warns_loudly(tmp_path: Path) -> None:
    """#744: the insecure opt-out is a visible startup warning."""
    result = _run(
        "mcp",
        "serve",
        "--catalog",
        str(gateway_catalog_path()),
        "--no-redact",
        "--dry-run",
    )
    assert result.returncode == 0, result.stderr
    assert "redaction is OFF" in result.stderr


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


def test_build_dispatch_config_builds_each_block() -> None:
    retry, rate_limits, cache = _build_dispatch_config(
        {
            "retry": {"max_attempts": 3, "base_delay": 0.2},
            "rate_limits": {"tool_execute": {"max_calls_per_session": 5}},
            "cache": {"read_only": True, "ttl_seconds": 30, "allow": ["files:read@1#0badc0de"]},
        }
    )
    assert retry is not None and retry.max_attempts == 3
    assert rate_limits is not None and rate_limits.enabled is True
    assert cache is not None and cache.enabled is True
    assert cache.allow is not None and "files:read@1#0badc0de" in cache.allow

    limiter, result_cache = _dispatch_behaviors(rate_limits, cache)
    assert limiter is not None
    assert result_cache is not None
    assert result_cache.admits("files:read@1#0badc0de") is True
    assert result_cache.admits("other:tool") is False


def test_build_dispatch_config_absent_blocks_are_none() -> None:
    assert _build_dispatch_config({}) == (None, None, None)
    assert _dispatch_behaviors(None, None) == (None, None)


def test_dispatch_behaviors_skips_cache_when_not_read_only() -> None:
    _, _, cache = _build_dispatch_config({"cache": {"ttl_seconds": 30}})
    assert cache is not None and cache.enabled is False
    _, result_cache = _dispatch_behaviors(None, cache)
    assert result_cache is None


def test_build_dispatch_config_rejects_string_cache_allow() -> None:
    # A bare string would otherwise collapse into a set of single characters.
    with pytest.raises(typer.BadParameter):
        _build_dispatch_config({"cache": {"read_only": True, "allow": "files:read"}})


def test_build_dispatch_config_rejects_bad_retryable_codes() -> None:
    with pytest.raises(typer.BadParameter):
        _build_dispatch_config({"retry": {"retryable_codes": "UPSTREAM_TIMEOUT"}})


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


# ------------------------------------------------------------------
# Gateway policy presets (#664)
# ------------------------------------------------------------------


def test_mcp_serve_help_shows_preset_flags() -> None:
    # Rich truncates long flag names/choice-lists at the default 80-column
    # width (no tty), so force a wide terminal to see the full flag text.
    result = _run("mcp", "serve", "--help", env_overrides={"COLUMNS": "200"})
    assert result.returncode == 0
    out = _strip_ansi(result.stdout)
    assert "--policy-preset" in out
    assert "--print-effective-policy" in out
    for name in GATEWAY_PRESET_NAMES:
        assert name in out


def test_load_serve_config_accepts_valid_policy_preset(tmp_path: Path) -> None:
    cfg = tmp_path / "gateway.yaml"
    cfg.write_text("catalog: cat.json\npolicy_preset: balanced\n", encoding="utf-8")
    (tmp_path / "cat.json").write_text("[]", encoding="utf-8")
    assert _load_serve_config(cfg)["policy_preset"] == "balanced"


def test_load_serve_config_rejects_unknown_policy_preset(tmp_path: Path) -> None:
    cfg = tmp_path / "gateway.yaml"
    cfg.write_text("catalog: cat.json\npolicy_preset: nope\n", encoding="utf-8")
    (tmp_path / "cat.json").write_text("[]", encoding="utf-8")
    with pytest.raises(typer.BadParameter):
        _load_serve_config(cfg)


def test_print_effective_policy_no_preset_is_inert_defaults() -> None:
    """No preset/config selected: the export mirrors byte-identical inert defaults."""
    result = _run("mcp", "serve", "--print-effective-policy")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["name"] == "custom"
    assert payload["policy"] == {"default": "allow", "rules": []}
    assert payload["retry"]["max_attempts"] == 1
    assert payload["rate_limits"] == {}
    assert payload["cache"]["read_only"] is False


def test_print_effective_policy_with_preset_does_not_require_catalog() -> None:
    result = _run("mcp", "serve", "--policy-preset", "safe", "--print-effective-policy")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["name"] == "safe"
    assert payload["policy"]["rules"][0]["action"] == "require_approval"
    assert payload["retry"]["max_attempts"] == 2
    assert payload["rate_limits"]["tool_execute"]["max_calls_per_minute"] == 30
    assert payload["cache"]["read_only"] is False


def test_print_effective_policy_matches_gateway_preset_to_dict() -> None:
    result = _run("mcp", "serve", "--policy-preset", "throughput", "--print-effective-policy")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    expected = GatewayPreset.from_preset("throughput").to_dict()
    assert payload == expected


def test_explicit_config_block_overrides_preset_wholesale(tmp_path: Path) -> None:
    """An explicit block wins over the preset's block; other blocks stay preset-sourced."""
    cfg = tmp_path / "gateway.yaml"
    cfg.write_text(
        "catalog: cat.json\n"
        "policy_preset: safe\n"
        "policy:\n"
        "  default: allow\n"
        "  rules: []\n"
        "retry:\n"
        "  max_attempts: 9\n",
        encoding="utf-8",
    )
    (tmp_path / "cat.json").write_text("[]", encoding="utf-8")
    result = _run("mcp", "serve", "--config", str(cfg), "--print-effective-policy")
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    # Explicit `policy`/`retry` blocks win wholesale.
    assert payload["policy"] == {"default": "allow", "rules": []}
    assert payload["retry"]["max_attempts"] == 9
    # `rate_limits`/`cache` were not overridden — still sourced from 'safe'.
    assert payload["rate_limits"]["tool_execute"]["max_calls_per_minute"] == 30
    assert payload["cache"]["read_only"] is False


def test_cli_policy_preset_wins_over_config_policy_preset(tmp_path: Path) -> None:
    """Explicit --policy-preset on the command line wins over the config key."""
    cfg = tmp_path / "gateway.yaml"
    cfg.write_text("catalog: cat.json\npolicy_preset: safe\n", encoding="utf-8")
    (tmp_path / "cat.json").write_text("[]", encoding="utf-8")
    result = _run(
        "mcp",
        "serve",
        "--config",
        str(cfg),
        "--policy-preset",
        "throughput",
        "--print-effective-policy",
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["name"] == "throughput"


def test_serve_dry_run_summary_reports_preset() -> None:
    catalog = gateway_catalog_path()
    result = _run(
        "mcp", "serve", "--catalog", str(catalog), "--policy-preset", "balanced", "--dry-run"
    )
    assert result.returncode == 0, result.stderr
    assert "preset=balanced" in (result.stderr + result.stdout)


def test_serve_dry_run_summary_reports_no_preset() -> None:
    catalog = gateway_catalog_path()
    result = _run("mcp", "serve", "--catalog", str(catalog), "--dry-run")
    assert result.returncode == 0, result.stderr
    assert "preset=none" in (result.stderr + result.stdout)


def test_build_runtime_wires_rate_limiter_and_cache_from_preset_config() -> None:
    """`_build_runtime` wires the RateLimiter/ToolResultCache _dispatch_behaviors builds."""
    preset = GatewayPreset.from_preset("throughput")
    rate_limiter, result_cache = _dispatch_behaviors(preset.rate_limits, preset.cache)
    assert rate_limiter is not None
    assert result_cache is not None
    runtime = _build_runtime(
        gateway_catalog_path(),
        mode=_ServeMode.gateway,
        top_k=10,
        beam_width=3,
        cache_stable=False,
        retry_policy=preset.retry,
        rate_limiter=rate_limiter,
        result_cache=result_cache,
    )
    assert runtime._retry_policy is preset.retry
    assert runtime._rate_limiter is rate_limiter
    assert runtime._result_cache is result_cache


def test_unknown_policy_preset_cli_value_rejected() -> None:
    result = _run(
        "mcp", "serve", "--catalog", str(gateway_catalog_path()), "--policy-preset", "nope"
    )
    assert result.returncode != 0
    assert "safe" in result.stderr and "balanced" in result.stderr


# ------------------------------------------------------------------
# Live multi-upstream config (issues #366/#368/#374/#375)
# ------------------------------------------------------------------


def test_load_serve_config_accepts_upstreams_without_catalog(tmp_path: Path) -> None:
    config_path = tmp_path / "gateway.json"
    config_path.write_text(json.dumps({"upstreams": {"fs": {"type": "stdio", "command": "echo"}}}))
    cfg = _load_serve_config(config_path)
    assert "catalog" not in cfg
    assert cfg["upstreams"] == {"fs": {"type": "stdio", "command": "echo"}}


def test_load_serve_config_rejects_neither_catalog_nor_upstreams(tmp_path: Path) -> None:
    config_path = tmp_path / "gateway.json"
    config_path.write_text(json.dumps({"mode": "gateway"}))
    with pytest.raises(typer.BadParameter, match="catalog.*or.*upstreams"):
        _load_serve_config(config_path)


def test_load_serve_config_rejects_non_mapping_upstreams_block(tmp_path: Path) -> None:
    config_path = tmp_path / "gateway.json"
    config_path.write_text(json.dumps({"upstreams": ["not", "a", "mapping"]}))
    with pytest.raises(typer.BadParameter, match="upstreams must be a mapping"):
        _load_serve_config(config_path)


def test_load_serve_config_rejects_both_catalog_and_upstreams(tmp_path: Path) -> None:
    # docs/gateway_spec.md §4.7 documents these as mutually exclusive; silently
    # preferring one over the other would be surprising behavior.
    config_path = tmp_path / "gateway.json"
    config_path.write_text(
        json.dumps(
            {
                "catalog": str(gateway_catalog_path()),
                "upstreams": {"fs": {"type": "stdio", "command": "echo"}},
            }
        )
    )
    with pytest.raises(typer.BadParameter, match="must not set both 'catalog' and 'upstreams'"):
        _load_serve_config(config_path)


def test_serve_live_upstream_malformed_config_reports_clean_error(tmp_path: Path) -> None:
    # A malformed `upstreams`/`startup`/`artifacts` block raises ConfigError
    # deep in parse_upstreams_config/StartupPolicy.from_dict/ArtifactPolicy.from_dict;
    # the CLI must convert it to a clean --config error, not a raw traceback.
    config_path = tmp_path / "gateway.json"
    config_path.write_text(
        json.dumps({"upstreams": {"fs": {"type": "carrier-pigeon", "command": "echo"}}})
    )
    result = subprocess.run(
        [sys.executable, "-m", "contextweaver", "mcp", "serve", "--config", str(config_path)],
        capture_output=True,
        text=True,
        timeout=25,
    )
    assert result.returncode != 0
    assert "Traceback" not in result.stderr
    assert "type must be one of" in result.stderr


def test_find_upstream_startup_error_bare_instance() -> None:
    from contextweaver.exceptions import UpstreamStartupError

    exc = UpstreamStartupError("boom")
    assert _find_upstream_startup_error(exc) is exc


def test_find_upstream_startup_error_unwraps_exception_group() -> None:
    from contextweaver.exceptions import UpstreamStartupError

    inner = UpstreamStartupError("boom")
    # Duck-typed stand-in for BaseExceptionGroup so this stays testable
    # without importing the 3.11+-only builtin directly.
    group = type("FakeGroup", (Exception,), {})()
    group.exceptions = (ValueError("unrelated"), inner)  # type: ignore[attr-defined]
    assert _find_upstream_startup_error(group) is inner


def test_find_upstream_startup_error_returns_none_for_unrelated_exception() -> None:
    assert _find_upstream_startup_error(ValueError("nope")) is None


_ECHO_UPSTREAM_SERVER = '''
from fastmcp import FastMCP

server = FastMCP(name="echo-server")


@server.tool
def echo(message: str) -> str:
    """Echo back the message."""
    return f"echo: {message}"


if __name__ == "__main__":
    server.run()
'''


def test_serve_live_upstream_end_to_end_over_real_subprocess(tmp_path: Path) -> None:
    """``mcp serve --config`` with an ``upstreams:`` block against a real
    stdio subprocess MCP server (issues #366/#368/#374): the upstream is
    connected, discovered, namespaced, and the gateway starts successfully.
    """
    server_script = tmp_path / "echo_server.py"
    server_script.write_text(_ECHO_UPSTREAM_SERVER)
    config_path = tmp_path / "gateway.json"
    config_path.write_text(
        json.dumps(
            {
                "upstreams": {
                    "echo": {
                        "type": "stdio",
                        "command": sys.executable,
                        "args": [str(server_script)],
                        "namespace": "echo",
                    }
                },
                "startup": {"mode": "strict", "min_healthy_upstreams": 1},
            }
        )
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "contextweaver",
            "mcp",
            "serve",
            "--config",
            str(config_path),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        timeout=25,
    )
    assert result.returncode == 0, result.stderr
    assert "upstream 'echo': loaded tools=1" in result.stderr
    assert "tools=1" in result.stderr
    assert "dry-run: upstreams validated" in result.stderr


def test_serve_live_upstream_strict_mode_reports_clean_error(tmp_path: Path) -> None:
    """A required upstream that fails to start under ``mode: strict`` produces
    a clean, single-line CLI error (not a raw traceback) and exits non-zero —
    regression for the anyio ExceptionGroup-wrapping behavior seen when
    ``AsyncExitStack`` unwinds other still-open upstreams during the raise.
    """
    config_path = tmp_path / "gateway.json"
    config_path.write_text(
        json.dumps(
            {
                "upstreams": {"broken": {"type": "stdio", "command": "false", "required": True}},
                "startup": {"mode": "strict"},
            }
        )
    )
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "contextweaver",
            "mcp",
            "serve",
            "--config",
            str(config_path),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        timeout=25,
    )
    assert result.returncode == 1
    assert "Traceback" not in result.stderr
    assert "CW_UPSTREAM_STARTUP" in result.stderr
    assert "required upstream(s) failed to start: broken" in result.stderr

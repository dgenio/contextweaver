"""Pure transform for ``contextweaver mcp import-vscode`` (#367).

Reads an existing VS Code (or VS Code-family) MCP config and produces:

1. A gateway config (``upstreams:`` block) contextweaver can serve directly
   via ``mcp serve --config``.
2. A replacement VS Code ``mcp.json`` that exposes only the
   ``contextweaver-gateway`` server, mirroring the shape
   :func:`contextweaver._mcp_cli._render_config_payload` already emits for
   the ``copilot`` target.

This module does no filesystem writing beyond :func:`write_migration`; the
CLI wrapper in :mod:`contextweaver._mcp_cli` owns argument parsing and
dry-run vs. ``--write`` gating.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from contextweaver.exceptions import ConfigError

#: Top-level keys VS Code-family configs use for the server map. VS Code
#: itself uses ``servers``; some hand-authored configs borrow Claude
#: Desktop's ``mcpServers`` — both are accepted on read.
_SERVER_MAP_KEYS: tuple[str, ...] = ("servers", "mcpServers")

_GATEWAY_SERVER_NAME = "contextweaver-gateway"


@dataclass
class VscodeMigrationPlan:
    """The result of transforming one VS Code MCP config (#367).

    Deliberately holds no rendered config dicts: the replacement client
    config embeds the *chosen* ``--gateway-config`` path, which is a CLI
    write-time decision, not something this pure transform can know in
    advance. Use :func:`render_gateway_config` / :func:`render_replacement_config`
    once that path is known.

    Attributes:
        server_map_key: Whichever of ``"servers"`` / ``"mcpServers"`` the
            source config used — the replacement config reuses it so the
            client (VS Code vs. a Claude-Desktop-shaped config) keeps
            recognising its own schema.
        upstreams: The ``upstreams`` block for the generated gateway config,
            keyed by the original server name.
        warnings: Non-fatal notes (e.g. an unsupported server was skipped).
        skipped: Names of servers that could not be migrated.
    """

    server_map_key: str
    upstreams: dict[str, dict[str, Any]]
    warnings: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def load_vscode_mcp_config(path: Path) -> dict[str, Any]:
    """Read and parse a VS Code-family MCP config file.

    Raises:
        ConfigError: If the file is missing, unparseable, or not a mapping.
    """
    if not path.exists():
        raise ConfigError(f"vscode mcp config not found: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ConfigError(f"invalid vscode mcp config {path}: {exc}") from exc
    if not isinstance(data, dict):
        raise ConfigError(f"vscode mcp config {path} must be a JSON mapping")
    return data


def _extract_server_map(vscode_config: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Return ``(key_used, server_map)`` from whichever recognised key is present."""
    for key in _SERVER_MAP_KEYS:
        value = vscode_config.get(key)
        if isinstance(value, dict) and value:
            return key, value
    raise ConfigError(
        f"vscode mcp config declares no servers under {' or '.join(_SERVER_MAP_KEYS)!r}"
    )


def _server_to_upstream(
    name: str,
    entry: Any,  # noqa: ANN401 — raw JSON server entry
) -> tuple[dict[str, Any] | None, str | None]:
    """Convert one VS Code server entry to an ``upstreams.<name>`` dict.

    Returns ``(upstream_dict, warning)``; *upstream_dict* is ``None`` when the
    entry cannot be migrated (unsupported shape), in which case *warning*
    explains why.
    """
    if not isinstance(entry, dict):
        return None, f"server {name!r}: not a mapping, skipped"
    server_type = str(entry.get("type", ""))
    if "command" in entry:
        upstream: dict[str, Any] = {
            "type": "stdio",
            "command": str(entry["command"]),
            "namespace": name,
        }
        if entry.get("args"):
            upstream["args"] = [str(a) for a in entry["args"]]
        if entry.get("env"):
            upstream["env"] = {str(k): str(v) for k, v in entry["env"].items()}
        return upstream, None
    if "url" in entry:
        upstream = {
            "type": "sse" if server_type == "sse" else "http",
            "url": str(entry["url"]),
            "namespace": name,
        }
        if entry.get("headers"):
            upstream["headers"] = {str(k): str(v) for k, v in entry["headers"].items()}
        return upstream, None
    return None, f"server {name!r}: unsupported shape (no 'command' or 'url'), skipped"


def build_migration_plan(vscode_config: dict[str, Any]) -> VscodeMigrationPlan:
    """Transform a parsed VS Code MCP config into a :class:`VscodeMigrationPlan`.

    Raises:
        ConfigError: If every server entry is unsupported, which would
            otherwise silently produce a gateway config with an empty
            ``upstreams`` block and a replacement client config that cannot
            serve anything.
    """
    server_map_key, servers = _extract_server_map(vscode_config)
    upstreams: dict[str, dict[str, Any]] = {}
    warnings: list[str] = []
    skipped: list[str] = []
    for name, entry in servers.items():
        upstream, warning = _server_to_upstream(str(name), entry)
        if upstream is None:
            skipped.append(str(name))
            if warning:
                warnings.append(warning)
            continue
        upstreams[str(name)] = upstream
    if not upstreams:
        raise ConfigError(
            f"no server could be migrated; skipped: {', '.join(sorted(skipped))} "
            f"({'; '.join(warnings)})"
        )
    return VscodeMigrationPlan(
        server_map_key=server_map_key, upstreams=upstreams, warnings=warnings, skipped=skipped
    )


def render_gateway_config(plan: VscodeMigrationPlan) -> dict[str, Any]:
    """Render the ``upstreams``-only gateway config for *plan*."""
    return {"upstreams": plan.upstreams}


def render_replacement_config(
    plan: VscodeMigrationPlan, *, gateway_config_arg: str
) -> dict[str, Any]:
    """Render the replacement client config exposing only ``contextweaver-gateway``.

    Args:
        plan: The migration plan from :func:`build_migration_plan`.
        gateway_config_arg: The ``--config`` value to embed — a
            workspace-relative or absolute string rendering of wherever the
            gateway config is actually written (the caller decides this;
            see :func:`contextweaver._mcp_cli._workspace_path` for the
            rendering convention the rest of the CLI already uses).

    The VS Code-specific ``"$schema"`` and ``"type": "stdio"`` fields are
    only emitted for a ``"servers"``-keyed source config (VS Code's own
    schema); an ``"mcpServers"``-keyed source (Cursor / Claude Desktop
    shapes, per :func:`contextweaver._mcp_cli._render_config_payload`'s
    ``cursor`` target) gets the simpler entry those clients expect instead.
    """
    is_vscode = plan.server_map_key == "servers"
    server_entry: dict[str, Any] = {
        **({"type": "stdio"} if is_vscode else {}),
        "command": "uvx",
        "args": ["contextweaver", "mcp", "serve", "--config", gateway_config_arg],
    }
    payload: dict[str, Any] = {
        **({"$schema": "https://aka.ms/vscode-mcp-schema"} if is_vscode else {}),
        plan.server_map_key: {_GATEWAY_SERVER_NAME: server_entry},
    }
    return payload


def render_dry_run_report(
    plan: VscodeMigrationPlan, *, gateway_config_path: Path, output_path: Path
) -> str:
    """Render a human-readable migration plan for ``--dry-run`` (the default)."""
    lines = [
        f"migrate {len(plan.upstreams)} server(s) -> {gateway_config_path}",
        *(f"  - {name}" for name in sorted(plan.upstreams)),
        f"replace {output_path} with a single 'contextweaver-gateway' entry",
    ]
    if plan.skipped:
        lines.append(f"skipped {len(plan.skipped)} server(s): {', '.join(sorted(plan.skipped))}")
    for warning in plan.warnings:
        lines.append(f"warning: {warning}")
    lines.append("(dry run — pass --write to create the files above)")
    return "\n".join(lines)


def write_migration(
    plan: VscodeMigrationPlan,
    *,
    gateway_config_path: Path,
    gateway_config_arg: str,
    output_path: Path,
    backup: bool = True,
) -> list[Path]:
    """Write the gateway config and replacement ``mcp.json``; returns paths written.

    Args:
        plan: The migration plan from :func:`build_migration_plan`.
        gateway_config_path: Where to write the new gateway config (JSON).
        gateway_config_arg: The ``--config`` value embedded in the
            replacement config's launch args (see :func:`render_replacement_config`).
        output_path: Where to write the replacement VS Code MCP config —
            typically the same path *vscode_config* was read from.
        backup: When ``True`` (default) and *output_path* already exists, it
            is copied to ``{output_path}.bak`` before being overwritten.

    Returns:
        Every path written, in write order: the gateway config, then the
        backup (if one was made), then the replacement client config.
    """
    written: list[Path] = []
    gateway_config_path.parent.mkdir(parents=True, exist_ok=True)
    gateway_config_path.write_text(
        json.dumps(render_gateway_config(plan), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    written.append(gateway_config_path)

    if backup and output_path.exists():
        backup_path = output_path.with_suffix(output_path.suffix + ".bak")
        shutil.copyfile(output_path, backup_path)
        written.append(backup_path)

    replacement = render_replacement_config(plan, gateway_config_arg=gateway_config_arg)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(replacement, indent=2) + "\n", encoding="utf-8")
    written.append(output_path)
    return written


__all__ = [
    "VscodeMigrationPlan",
    "build_migration_plan",
    "load_vscode_mcp_config",
    "render_dry_run_report",
    "render_gateway_config",
    "render_replacement_config",
    "write_migration",
]

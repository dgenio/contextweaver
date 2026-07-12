"""Tests for the ``contextweaver mcp generate-configs`` subcommand."""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_RECIPES_DIR = _REPO_ROOT / "examples" / "recipes"
_ALL_CONFIG_FILES = [
    "copilot_mcp.json",
    "cursor_mcp.json",
    "claude_desktop_config.json",
    "claude_code_mcp.json",
]

# ANSI escape code regex for stripping color codes from help output
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def _strip_ansi(text: str) -> str:
    """Remove ANSI escape codes from text."""
    return _ANSI_ESCAPE.sub("", text)


def _run(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess[str]:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env["NO_COLOR"] = "1"  # Disable ANSI color codes for test assertions
    return subprocess.run(
        [sys.executable, "-m", "contextweaver", *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=str(cwd or _REPO_ROOT),
        env=env,
    )


def test_mcp_help_lists_generate_configs_subcommand() -> None:
    result = _run("mcp", "--help")
    assert result.returncode == 0
    out = _strip_ansi(result.stdout + result.stderr)
    assert "generate-configs" in out


def test_generate_configs_help_lists_core_options() -> None:
    result = _run("mcp", "generate-configs", "--help")
    assert result.returncode == 0
    out = _strip_ansi(result.stdout + result.stderr)
    assert "--config" in out
    assert "--out-dir" in out
    assert "--target" in out
    assert "--force" in out


def test_generate_configs_defaults_emit_all_artifacts(tmp_path: Path) -> None:
    config = (_RECIPES_DIR / "gateway_config.yaml").resolve()
    result = _run(
        "mcp",
        "generate-configs",
        "--config",
        str(config),
        "--out-dir",
        str(tmp_path),
    )
    assert result.returncode == 0, result.stderr

    for filename in _ALL_CONFIG_FILES:
        assert (tmp_path / filename).is_file()

    out = result.stdout + result.stderr
    for target in ("copilot", "cursor", "claude_desktop", "claude_code"):
        assert f"warning [{target}]" in out


def test_generate_configs_selected_targets_only(tmp_path: Path) -> None:
    config = (_RECIPES_DIR / "gateway_config.yaml").resolve()
    result = _run(
        "mcp",
        "generate-configs",
        "--config",
        str(config),
        "--out-dir",
        str(tmp_path),
        "--target",
        "copilot",
        "--target",
        "cursor",
    )
    assert result.returncode == 0, result.stderr

    actual = sorted(path.name for path in tmp_path.iterdir())
    assert actual == ["copilot_mcp.json", "cursor_mcp.json"]


def test_generate_configs_rejects_existing_without_force(tmp_path: Path) -> None:
    existing = tmp_path / "copilot_mcp.json"
    existing.write_text("sentinel\n", encoding="utf-8")

    config = (_RECIPES_DIR / "gateway_config.yaml").resolve()
    result = _run(
        "mcp",
        "generate-configs",
        "--config",
        str(config),
        "--out-dir",
        str(tmp_path),
        "--target",
        "copilot",
    )
    assert result.returncode != 0
    assert "overwrite" in (result.stdout + result.stderr).lower()
    assert existing.read_text(encoding="utf-8") == "sentinel\n"


def test_generate_configs_validates_config_before_writing(tmp_path: Path) -> None:
    bad = tmp_path / "bad.yaml"
    bad.write_text("mode: gateway\n", encoding="utf-8")
    out_dir = tmp_path / "out"

    result = _run(
        "mcp",
        "generate-configs",
        "--config",
        str(bad),
        "--out-dir",
        str(out_dir),
    )
    assert result.returncode != 0
    assert "catalog" in (result.stdout + result.stderr).lower()
    assert not out_dir.exists()


def test_generate_configs_matches_shipped_recipe_fixtures(tmp_path: Path) -> None:
    config = (_RECIPES_DIR / "gateway_config.yaml").resolve()
    result = _run(
        "mcp",
        "generate-configs",
        "--config",
        str(config),
        "--out-dir",
        str(tmp_path),
    )
    assert result.returncode == 0, result.stderr

    for filename in _ALL_CONFIG_FILES:
        generated = (tmp_path / filename).read_text(encoding="utf-8")
        expected = (_RECIPES_DIR / filename).read_text(encoding="utf-8")
        assert generated == expected


def test_generate_configs_force_overwrites_existing(tmp_path: Path) -> None:
    existing = tmp_path / "copilot_mcp.json"
    existing.write_text("sentinel\n", encoding="utf-8")

    config = (_RECIPES_DIR / "gateway_config.yaml").resolve()
    result = _run(
        "mcp",
        "generate-configs",
        "--config",
        str(config),
        "--out-dir",
        str(tmp_path),
        "--target",
        "copilot",
        "--force",
    )
    assert result.returncode == 0, result.stderr

    overwritten = existing.read_text(encoding="utf-8")
    assert overwritten != "sentinel\n"
    assert "contextweaver-gateway" in overwritten


def test_generate_configs_outside_workspace_emits_absolute_paths(tmp_path: Path) -> None:
    # A config that lives outside the invocation cwd cannot be expressed as a
    # ${workspaceFolder}-relative path, so the command must fall back to an
    # absolute path and surface a compatibility warning.
    config = tmp_path / "gateway_config.yaml"
    config.write_text("catalog: ./catalog.json\nmode: gateway\n", encoding="utf-8")
    out_dir = tmp_path / "out"

    result = _run(
        "mcp",
        "generate-configs",
        "--config",
        str(config),
        "--out-dir",
        str(out_dir),
        "--target",
        "copilot",
        cwd=_REPO_ROOT,
    )
    assert result.returncode == 0, result.stderr
    assert "outside the current workspace" in _strip_ansi(result.stdout + result.stderr)

    copilot = (out_dir / "copilot_mcp.json").read_text(encoding="utf-8")
    # Absolute config path is emitted verbatim instead of a ${workspaceFolder}
    # token. The generator normalises to POSIX separators (``as_posix()``), so
    # compare against that form rather than ``str()`` — on Windows the latter
    # uses backslashes and would spuriously fail (issue #749).
    assert config.resolve().as_posix() in copilot
    assert "${workspaceFolder}" not in copilot

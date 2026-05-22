"""Tests for ``examples/recipes/serve_gateway.py`` (#278, #279).

The launcher is the load-bearing piece both recipes point at: the
Claude Desktop and VS Code Copilot configs invoke it directly. A
regression in its snapshot loader, runtime builder, or exit codes
would silently break every downstream user. These tests pin the
public surface without spawning the MCP stdio transport (which is
covered upstream by the gateway server's own tests).
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT_PATH = _REPO_ROOT / "examples" / "recipes" / "serve_gateway.py"


def _load_launcher() -> ModuleType:
    """Import the launcher as a module without altering ``sys.path``."""
    spec = importlib.util.spec_from_file_location("recipes_serve_gateway", _SCRIPT_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


serve_gateway = _load_launcher()


# ------------------------------------------------------------------
# _load_snapshot_tools
# ------------------------------------------------------------------


def test_load_snapshot_tools_accepts_main_catalog_shape(tmp_path: Path) -> None:
    """The loader accepts the ``{_source, _captured_with, tools}`` shape."""
    payload = {
        "_source": "https://example.com/server",
        "_captured_with": "scripts/capture_mcp_catalog.py",
        "tools": [
            {"name": "echo", "description": "Echo a message.", "inputSchema": {}},
        ],
    }
    path = tmp_path / "ok.json"
    path.write_text(json.dumps(payload))
    tools = serve_gateway._load_snapshot_tools(path)
    assert tools == payload["tools"]


def test_load_snapshot_tools_accepts_meta_block_shape(tmp_path: Path) -> None:
    """The loader still works against the legacy ``{_meta, tools}`` shape."""
    payload = {
        "_meta": {"source": "example", "license": "MIT"},
        "tools": [{"name": "now", "description": "Time.", "inputSchema": {}}],
    }
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps(payload))
    assert serve_gateway._load_snapshot_tools(path) == payload["tools"]


def test_load_snapshot_tools_rejects_missing_tools_key(tmp_path: Path) -> None:
    """A payload without ``tools`` raises ``RuntimeError`` (not SystemExit)."""
    path = tmp_path / "no_tools.json"
    path.write_text(json.dumps({"_meta": {"source": "x"}}))
    with pytest.raises(RuntimeError, match="malformed snapshot"):
        serve_gateway._load_snapshot_tools(path)


def test_load_snapshot_tools_rejects_non_list_tools(tmp_path: Path) -> None:
    """A ``tools`` field that is not a list raises ``RuntimeError``."""
    path = tmp_path / "bad_tools.json"
    path.write_text(json.dumps({"tools": {"name": "wrong-shape"}}))
    with pytest.raises(RuntimeError, match="not a list"):
        serve_gateway._load_snapshot_tools(path)


def test_load_snapshot_tools_rejects_non_object_payload(tmp_path: Path) -> None:
    """A top-level array (rather than object) raises ``RuntimeError``."""
    path = tmp_path / "array.json"
    path.write_text(json.dumps([{"name": "x", "description": "y", "inputSchema": {}}]))
    with pytest.raises(RuntimeError, match="malformed snapshot"):
        serve_gateway._load_snapshot_tools(path)


def test_load_snapshot_tools_rejects_invalid_json(tmp_path: Path) -> None:
    """Malformed JSON surfaces as ``json.JSONDecodeError`` (caught by ``main``)."""
    path = tmp_path / "broken.json"
    path.write_text("{not json")
    with pytest.raises(json.JSONDecodeError):
        serve_gateway._load_snapshot_tools(path)


# ------------------------------------------------------------------
# build_runtime_from_snapshot / build_stub_runtime
# ------------------------------------------------------------------


def test_build_runtime_from_snapshot_registers_tools(tmp_path: Path) -> None:
    """The runtime exposes every tool from the snapshot via ``list_tool_ids``."""
    payload = {
        "tools": [
            {"name": "alpha", "description": "a", "inputSchema": {}},
            {"name": "beta", "description": "b", "inputSchema": {}},
            {"name": "gamma", "description": "c", "inputSchema": {}},
        ]
    }
    path = tmp_path / "three.json"
    path.write_text(json.dumps(payload))
    runtime = serve_gateway.build_runtime_from_snapshot(path)
    tool_ids = runtime.list_tool_ids()
    # Tool ids are canonical (namespace.name#hash); just check we have 3 of them.
    assert len(tool_ids) == 3


def test_build_stub_runtime_exposes_echo_and_now() -> None:
    """The stub runtime ships the two documented sanity-check tools."""
    runtime = serve_gateway.build_stub_runtime()
    assert len(runtime.list_tool_ids()) == 2
    # The stub catalog is owned by the launcher; assert the names are stable.
    stub_names = {tool["name"] for tool in serve_gateway._STUB_TOOLS}
    assert stub_names == {"echo", "now"}


# ------------------------------------------------------------------
# _parse_args
# ------------------------------------------------------------------


def test_parse_args_requires_stub_or_catalog() -> None:
    """``--stub`` and ``--catalog`` are mutually exclusive; one must be present."""
    with pytest.raises(SystemExit):
        serve_gateway._parse_args([])


def test_parse_args_rejects_stub_and_catalog_together(tmp_path: Path) -> None:
    """Both flags at once is a CLI usage error."""
    with pytest.raises(SystemExit):
        serve_gateway._parse_args(["--stub", "--catalog", str(tmp_path / "x.json")])


def test_parse_args_defaults_server_name() -> None:
    """The ``--name`` default is the documented gateway name."""
    args = serve_gateway._parse_args(["--stub"])
    assert args.name == "contextweaver-recipes-gateway"
    assert args.stub is True


# ------------------------------------------------------------------
# main() exit-code contract (no real stdio transport)
# ------------------------------------------------------------------


def _close_coro(coro: object) -> None:
    """Replacement for ``asyncio.run`` that closes the coroutine cleanly.

    The launcher hands ``asyncio.run`` a freshly-created coroutine; the
    transport tests don't want to actually bind stdio, but if we just
    discard the coroutine Python emits a ``RuntimeWarning: coroutine was
    never awaited``. ``close()`` releases it deterministically.
    """
    close = getattr(coro, "close", None)
    if callable(close):
        close()


def _close_coro_then_keyboard_interrupt(coro: object) -> None:
    _close_coro(coro)
    raise KeyboardInterrupt


def _close_coro_then_transport_error(coro: object) -> None:
    _close_coro(coro)
    raise RuntimeError("stdio transport broke")


def test_main_returns_one_on_malformed_snapshot(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A malformed snapshot must surface as ``return 1``, not a SystemExit / crash."""
    # Replace asyncio.run so the test never actually binds stdio.
    monkeypatch.setattr(serve_gateway.asyncio, "run", _close_coro)
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"_meta": {"source": "x"}}))  # no 'tools' key
    exit_code = serve_gateway.main(["--catalog", str(bad)])
    assert exit_code == 1


def test_main_returns_zero_on_stub_clean_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    """The stub catalog launches cleanly when the transport stub returns immediately."""
    monkeypatch.setattr(serve_gateway.asyncio, "run", _close_coro)
    exit_code = serve_gateway.main(["--stub"])
    assert exit_code == 0


def test_main_returns_zero_on_keyboard_interrupt(monkeypatch: pytest.MonkeyPatch) -> None:
    """``Ctrl-C`` while serving stdio is a clean disconnect (exit 0)."""
    monkeypatch.setattr(serve_gateway.asyncio, "run", _close_coro_then_keyboard_interrupt)
    exit_code = serve_gateway.main(["--stub"])
    assert exit_code == 0


def test_main_returns_one_on_transport_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """A transport-level failure during ``asyncio.run`` maps to ``return 1``."""
    monkeypatch.setattr(serve_gateway.asyncio, "run", _close_coro_then_transport_error)
    exit_code = serve_gateway.main(["--stub"])
    assert exit_code == 1

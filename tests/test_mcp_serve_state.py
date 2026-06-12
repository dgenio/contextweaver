"""Persistent ``mcp serve`` state via ``--state-dir`` (issue #511).

Covers config parsing, store wiring, restart rehydration (artifact handles +
event history survive recreating the runtime against the same directory), and
the unwritable-directory error path.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
import typer

from contextweaver._mcp_cli import (
    _build_runtime,
    _build_state_stores,
    _load_serve_config,
    _ServeMode,
)
from contextweaver.data import gateway_catalog_path
from contextweaver.store.json_file_artifacts import JsonFileArtifactStore
from contextweaver.store.sqlite_event_log import SqliteEventLog
from contextweaver.types import ContextItem, ItemKind

_MODE = _ServeMode


def _catalog() -> Path:
    return Path(gateway_catalog_path())


# ---------------------------------------------------------------------------
# Config parsing
# ---------------------------------------------------------------------------


def test_state_dir_is_accepted_and_resolved_from_config_dir(tmp_path: Path) -> None:
    cfg = tmp_path / "gateway.json"
    cfg.write_text(json.dumps({"catalog": str(_catalog()), "state_dir": "state"}))
    data = _load_serve_config(cfg)
    # Relative state_dir resolves against the config file's directory.
    assert data["state_dir"] == str((tmp_path / "state").resolve())


def test_unknown_config_key_still_rejected(tmp_path: Path) -> None:
    cfg = tmp_path / "gateway.json"
    cfg.write_text(json.dumps({"catalog": str(_catalog()), "bogus": 1}))
    with pytest.raises(typer.BadParameter):
        _load_serve_config(cfg)


# ---------------------------------------------------------------------------
# Store wiring + layout
# ---------------------------------------------------------------------------


def test_build_state_stores_lays_out_files(tmp_path: Path) -> None:
    bundle = _build_state_stores(tmp_path / "state")
    assert isinstance(bundle.event_log, SqliteEventLog)
    assert isinstance(bundle.artifact_store, JsonFileArtifactStore)
    assert (tmp_path / "state" / "events.sqlite3").is_file()
    assert (tmp_path / "state" / "artifacts").is_dir()
    bundle.event_log.close()


def test_build_runtime_without_state_dir_uses_in_memory() -> None:
    runtime = _build_runtime(
        _catalog(), mode=_MODE.gateway, top_k=5, beam_width=3, cache_stable=False
    )
    # Default in-memory artifact store has no on-disk base_dir attribute.
    assert not isinstance(runtime.context_manager.artifact_store, JsonFileArtifactStore)


def test_build_runtime_with_state_dir_wires_persistent_stores(tmp_path: Path) -> None:
    runtime = _build_runtime(
        _catalog(),
        mode=_MODE.gateway,
        top_k=5,
        beam_width=3,
        cache_stable=False,
        state_dir=tmp_path / "state",
    )
    assert isinstance(runtime.context_manager.artifact_store, JsonFileArtifactStore)
    assert isinstance(runtime.context_manager.event_log, SqliteEventLog)


# ---------------------------------------------------------------------------
# Restart rehydration
# ---------------------------------------------------------------------------


def test_artifact_handle_survives_restart(tmp_path: Path) -> None:
    state = tmp_path / "state"
    first = _build_runtime(
        _catalog(), mode=_MODE.gateway, top_k=5, beam_width=3, cache_stable=False, state_dir=state
    )
    first.context_manager.artifact_store.put(
        "artifact:result:call_1", b"the full upstream payload", media_type="text/plain"
    )
    first.context_manager.event_log.close()

    # A fresh runtime over the same state_dir rehydrates the handle.
    second = _build_runtime(
        _catalog(), mode=_MODE.gateway, top_k=5, beam_width=3, cache_stable=False, state_dir=state
    )
    store = second.context_manager.artifact_store
    assert store.exists("artifact:result:call_1") is True
    assert store.get("artifact:result:call_1") == b"the full upstream payload"
    second.context_manager.event_log.close()


def test_event_history_survives_restart(tmp_path: Path) -> None:
    state = tmp_path / "state"
    first = _build_runtime(
        _catalog(), mode=_MODE.gateway, top_k=5, beam_width=3, cache_stable=False, state_dir=state
    )
    first.context_manager.event_log.append(
        ContextItem(id="turn-1", kind=ItemKind.user_turn, text="hello")
    )
    first.context_manager.event_log.close()

    second = _build_runtime(
        _catalog(), mode=_MODE.gateway, top_k=5, beam_width=3, cache_stable=False, state_dir=state
    )
    assert second.context_manager.event_log.count() == 1
    assert second.context_manager.event_log.get("turn-1").text == "hello"
    second.context_manager.event_log.close()


# ---------------------------------------------------------------------------
# Error path
# ---------------------------------------------------------------------------


def test_unwritable_state_dir_raises_bad_parameter(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("i am a file, not a directory")
    # state_dir under a regular file cannot be created -> clear startup error.
    with pytest.raises(typer.BadParameter):
        _build_state_stores(blocker / "state")

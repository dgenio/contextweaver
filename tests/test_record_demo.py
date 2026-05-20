"""Tests for scripts/record_demo.py (issue #281)."""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _REPO_ROOT / "scripts" / "record_demo.py"
_CASTS_DIR = _REPO_ROOT / "docs" / "assets" / "casts"

_spec = importlib.util.spec_from_file_location("record_demo", _SCRIPT)
assert _spec is not None and _spec.loader is not None
record_demo = importlib.util.module_from_spec(_spec)
# Register before exec so the dataclass decorator can resolve forward
# references through ``sys.modules`` introspection.
sys.modules["record_demo"] = record_demo
_spec.loader.exec_module(record_demo)


def test_committed_casts_exist() -> None:
    """All four showcase demos must ship a committed cast."""
    expected = {f"{d.name}.cast" for d in record_demo._DEMOS}
    actual = {p.name for p in _CASTS_DIR.glob("*.cast")}
    assert expected.issubset(actual), f"missing casts: {expected - actual}"


@pytest.mark.parametrize("demo", record_demo._DEMOS, ids=lambda d: str(d.name))
def test_each_cast_is_valid_v2_jsonl(demo: record_demo.Demo) -> None:
    """Every committed cast must parse as asciinema v2 JSONL —
    line 1 is the JSON header, subsequent lines are [t, "o", text] triples."""
    cast_path = _CASTS_DIR / f"{demo.name}.cast"
    lines = cast_path.read_text(encoding="utf-8").splitlines()
    assert lines, f"{cast_path} is empty"
    header = json.loads(lines[0])
    assert header["version"] == 2
    assert header["title"] == demo.description
    assert isinstance(header["width"], int) and header["width"] > 0
    assert isinstance(header["height"], int) and header["height"] > 0
    for idx, line in enumerate(lines[1:], start=2):
        event = json.loads(line)
        assert isinstance(event, list) and len(event) == 3, (
            f"{cast_path}:{idx}: event must be [t, channel, text]"
        )
        assert isinstance(event[0], (int, float)) and event[0] >= 0
        assert event[1] == "o", f"{cast_path}:{idx}: channel must be 'o'"
        assert isinstance(event[2], str)


@pytest.mark.parametrize("demo", record_demo._DEMOS, ids=lambda d: str(d.name))
def test_event_timestamps_are_monotonic_non_decreasing(demo: record_demo.Demo) -> None:
    """Event timestamps must be monotonic — otherwise asciinema-player will
    refuse to play the cast."""
    cast_path = _CASTS_DIR / f"{demo.name}.cast"
    events = [json.loads(line) for line in cast_path.read_text(encoding="utf-8").splitlines()[1:]]
    times = [ev[0] for ev in events]
    for prev, curr in zip(times, times[1:], strict=False):
        assert curr >= prev, f"timestamps regressed in {cast_path}: {prev} -> {curr}"


def test_check_mode_passes_on_committed_casts() -> None:
    """Running --check against the committed casts must exit 0 — the
    drift gate is what guarantees the committed casts stay in sync with
    the demos' stdout."""
    result = subprocess.run(
        [sys.executable, str(_SCRIPT), "--check"],
        check=False,
        capture_output=True,
        text=True,
        cwd=str(_REPO_ROOT),
        timeout=120,
    )
    assert result.returncode == 0, (
        f"--check failed; stderr=\n{result.stderr}\nstdout=\n{result.stdout}"
    )


def test_synthesise_cast_is_deterministic() -> None:
    """Two calls to _synthesise_cast on the same input must produce
    byte-identical output (no clocks, no rng)."""
    demo = record_demo._DEMOS[0]
    stdout = "hello\nworld\n"
    a = record_demo._synthesise_cast(demo, stdout)
    b = record_demo._synthesise_cast(demo, stdout)
    assert a == b


def test_synthesise_cast_handles_empty_stdout() -> None:
    """An empty-output demo must still produce a valid cast (degenerate
    case — corner of the v2 contract)."""
    demo = record_demo._DEMOS[0]
    cast = record_demo._synthesise_cast(demo, "")
    lines = cast.splitlines()
    # Header + opening prompt + placeholder event + closing prompt = 4
    assert len(lines) >= 3
    json.loads(lines[0])  # header parses
    for line in lines[1:]:
        evt = json.loads(line)
        assert isinstance(evt, list) and len(evt) == 3


def test_tiktoken_warning_line_is_filtered() -> None:
    """The CDN-flaky tiktoken warning must not appear in committed casts —
    otherwise CI would record a different cast than dev machines."""
    for cast_path in _CASTS_DIR.glob("*.cast"):
        text = cast_path.read_text(encoding="utf-8")
        assert "tiktoken cl100k_base encoding unavailable" not in text, (
            f"committed cast {cast_path.name} contains the tiktoken-CDN noise "
            "line — re-run scripts/record_demo.py with the filter intact"
        )

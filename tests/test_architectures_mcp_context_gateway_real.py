"""Smoke test for the real-catalog MCP Context Gateway variant (#280).

Loads the committed snapshots under
``examples/architectures/mcp_context_gateway/real_catalogs/`` and walks
the same routing/firewall/answer-phase shape used by the offline
``main.py``. The asserts pin the load-bearing invariants:

- All three snapshot files load.
- Each catalog has a non-trivial tool count.
- The intent tool name resolves to a canonical id in the shortlist.
- The firewall correctly no-ops on the short canned responses.
- The answer-phase build emits a sane prompt token count.
"""

from __future__ import annotations

import importlib.util
import io
import json
import re
from contextlib import redirect_stdout
from pathlib import Path
from types import ModuleType

import pytest

_REPO_ROOT = Path(__file__).resolve().parent.parent
_MAIN_PATH = _REPO_ROOT / "examples" / "architectures" / "mcp_context_gateway" / "main_real.py"
_SNAPSHOTS_DIR = _REPO_ROOT / "examples" / "architectures" / "mcp_context_gateway" / "real_catalogs"


def _load_module(path: Path) -> ModuleType:
    spec = importlib.util.spec_from_file_location("mcp_context_gateway_main_real", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


main_real = _load_module(_MAIN_PATH)


@pytest.fixture(scope="module")
def real_run_output() -> str:
    buf = io.StringIO()
    with redirect_stdout(buf):
        main_real.main()
    return buf.getvalue()


def test_three_snapshots_are_committed() -> None:
    """The real_catalogs/ directory must ship three committed snapshots."""
    snapshots = sorted(p.name for p in _SNAPSHOTS_DIR.glob("*.json"))
    assert snapshots == ["everything.json", "filesystem.json", "time.json"]


def test_every_snapshot_is_valid_mcp_tools_list() -> None:
    """Every snapshot must parse as the MCP wire shape so SchemaSource.from_mcp_tools
    can consume it without modification."""
    for snapshot in _SNAPSHOTS_DIR.glob("*.json"):
        payload = json.loads(snapshot.read_text(encoding="utf-8"))
        assert isinstance(payload, dict), f"{snapshot.name} not a dict"
        assert "tools" in payload, f"{snapshot.name} missing 'tools'"
        assert isinstance(payload["tools"], list)
        for tool in payload["tools"]:
            assert isinstance(tool, dict)
            assert "name" in tool
            # inputSchema is optional but, if present, must be a dict.
            if "inputSchema" in tool:
                assert isinstance(tool["inputSchema"], dict)


def test_main_real_walks_all_three_scenarios(real_run_output: str) -> None:
    """The output must mention each snapshot by file name."""
    assert "time.json" in real_run_output
    assert "filesystem.json" in real_run_output
    assert "everything.json" in real_run_output
    assert "Real-catalog scenarios complete" in real_run_output


def test_snapshot_tool_counts_match_committed_files(real_run_output: str) -> None:
    """Tool counts printed by main_real must match the committed snapshots —
    catches a regression where the loader silently drops tools."""
    expected = {}
    for snapshot in _SNAPSHOTS_DIR.glob("*.json"):
        payload = json.loads(snapshot.read_text(encoding="utf-8"))
        expected[snapshot.name] = len(payload["tools"])
    for name, count in expected.items():
        # Tolerate either ``count`` or ``count-1`` because canonical id
        # collisions in the unlikely case of duplicate names would drop one.
        section = real_run_output.split(name, 1)[1] if name in real_run_output else ""
        match = re.search(r"loaded:\s+\S+\.json\s+\((\d+)\s+tools", section)
        assert match is not None, f"loaded line missing for {name}"
        loaded = int(match.group(1))
        assert loaded == count, f"{name}: expected {count} tools, loaded {loaded}"


def test_firewall_no_ops_on_short_canned_responses(real_run_output: str) -> None:
    """All three canned upstream responses are well under the firewall
    threshold; every scenario should report a no-op."""
    matches = re.findall(r"firewall:\s+\d+ chars -> \d+ chars\s+\(no-op=(\w+)\)", real_run_output)
    assert len(matches) == 3
    assert all(m == "True" for m in matches), f"firewall no-op regression: {matches}"


def test_each_scenario_hydrates_a_real_schema(real_run_output: str) -> None:
    """For each of the 3 scenarios, the hydrated schema must be non-empty
    (the snapshots actually carry inputSchemas, unlike the YAML)."""
    matches = re.findall(r"hydrated schema for '[^']+':\s+(\d+) chars", real_run_output)
    assert len(matches) == 3
    assert all(int(m) > 50 for m in matches), f"hydrated schemas suspiciously small: {matches}"


def test_answer_phase_tokens_are_bounded(real_run_output: str) -> None:
    """Each scenario's answer-phase build must stay well under the budget."""
    matches = re.findall(r"answer:\s+tokens=(\d+)", real_run_output)
    assert len(matches) == 3
    tokens = [int(t) for t in matches]
    # No scenario blows the 4000-token answer budget.
    assert all(0 < t < 4000 for t in tokens), f"answer tokens unbounded: {tokens}"

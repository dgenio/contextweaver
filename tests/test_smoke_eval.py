"""Tests for the optional smoke-evaluation suite (issue #331).

The suite must stay deterministic and credential-free by default, keep the
model-dependent section off unless explicitly enabled, and exit 0 when every
deterministic check passes.
"""

from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

_ROOT = Path(__file__).resolve().parent.parent
_SCRIPT = _ROOT / "benchmarks" / "smoke_eval.py"


def _load_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("smoke_eval", _SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    # Register before exec so dataclass annotation resolution can find the
    # module in sys.modules (the script uses ``from __future__ annotations``).
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_all_deterministic_checks_pass() -> None:
    module = _load_module()
    results = module.run_deterministic()
    assert len(results) >= 3  # acceptance criterion: at least 3 fixed scenarios
    failed = [r.name for r in results if not r.passed]
    assert not failed, f"deterministic checks failed: {failed}"


def test_model_dependent_off_by_default() -> None:
    module = _load_module()
    # No CW_SMOKE_LLM in the ambient test environment.
    assert os.environ.get("CW_SMOKE_LLM") != "1"
    assert module.model_dependent_enabled() is False


def test_script_runs_clean_without_credentials() -> None:
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    env.pop("CW_SMOKE_LLM", None)
    result = subprocess.run(
        [sys.executable, str(_SCRIPT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env=env,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "RESULT: OK" in result.stdout
    # Deterministic and model-dependent sections are reported separately.
    assert "Deterministic checks" in result.stdout
    assert "Model-dependent checks" in result.stdout
    assert "[SKIP] disabled by default" in result.stdout

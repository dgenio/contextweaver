"""Tests for the agent-safe evaluation-artifact context profile (#335).

Covers the three risk levels (``ok`` / ``caution`` / ``high_risk``) and the
two safety invariants the profile guarantees:

1. ``V_hat`` is never presented without a support-health item earlier in the
   same compiled list, and never at all in the route phase.
2. High-risk artifacts foreground caveats before the estimate.
"""

from __future__ import annotations

import importlib.util
import io
import json
from contextlib import redirect_stdout
from pathlib import Path

import pytest

from contextweaver.types import Phase

_REPO_ROOT = Path(__file__).resolve().parent.parent
_ARCH_DIR = _REPO_ROOT / "examples" / "architectures" / "eval_artifact_profile"
_MAIN_PATH = _ARCH_DIR / "main.py"
_spec = importlib.util.spec_from_file_location("eval_artifact_profile_main", _MAIN_PATH)
assert _spec is not None and _spec.loader is not None
profile = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(profile)

_STATUSES = ["ok", "caution", "high_risk"]


def _load(status: str) -> dict:
    return json.loads((_ARCH_DIR / "fixtures" / f"artifact_{status}.json").read_text("utf-8"))


def _roles(items: list) -> list[str]:
    return [str(it.metadata.get("role", "")) for it in items]


@pytest.mark.parametrize("status", _STATUSES)
def test_route_phase_never_exposes_the_estimate(status: str) -> None:
    """The route phase must only carry summary metadata, never ``V_hat``."""
    items = profile.compile_eval_context(_load(status), Phase.route)
    assert profile.ROLE_VALUE not in _roles(items)
    rendered = " ".join(it.text for it in items)
    assert "V_hat" not in rendered


@pytest.mark.parametrize("status", _STATUSES)
def test_estimate_never_appears_without_support_diagnostics(status: str) -> None:
    """In any phase, a value estimate must be preceded by support health."""
    for phase in (Phase.route, Phase.interpret, Phase.answer):
        roles = _roles(profile.compile_eval_context(_load(status), phase))
        if profile.ROLE_VALUE in roles:
            assert profile.ROLE_SUPPORT in roles
            assert roles.index(profile.ROLE_SUPPORT) < roles.index(profile.ROLE_VALUE)


def test_interpret_phase_surfaces_the_estimate_for_every_status() -> None:
    """Interpret is the full diagnostic view — the estimate is present there."""
    for status in _STATUSES:
        roles = _roles(profile.compile_eval_context(_load(status), Phase.interpret))
        assert profile.ROLE_VALUE in roles
        assert roles[0] == profile.ROLE_SUPPORT  # support health is always first


def test_ok_answer_includes_estimate_but_risky_answers_withhold_it() -> None:
    """The human-facing answer leads with the estimate only when it is safe."""
    ok_roles = _roles(profile.compile_eval_context(_load("ok"), Phase.answer))
    assert profile.ROLE_VALUE in ok_roles
    for status in ("caution", "high_risk"):
        roles = _roles(profile.compile_eval_context(_load(status), Phase.answer))
        assert profile.ROLE_VALUE not in roles


def test_high_risk_foregrounds_caveats_before_the_estimate() -> None:
    """A high-risk interpret build must place caveats before ``V_hat``."""
    roles = _roles(profile.compile_eval_context(_load("high_risk"), Phase.interpret))
    assert profile.ROLE_CAVEAT in roles and profile.ROLE_VALUE in roles
    assert roles.index(profile.ROLE_CAVEAT) < roles.index(profile.ROLE_VALUE)


@pytest.mark.parametrize("status", _STATUSES)
def test_check_invariants_passes_for_every_fixture(status: str) -> None:
    """``check_invariants`` returns only PASS lines and never raises."""
    lines = profile.check_invariants(_load(status))
    assert lines
    assert all(line.strip().startswith("[PASS]") for line in lines)


def test_check_invariants_raises_when_estimate_precedes_support() -> None:
    """A tampered profile (estimate before support) must trip the assertion."""
    bad = _load("ok")
    # Drop support health entirely -> interpret would surface V_hat with no
    # support diagnostic, which the invariant must reject.
    bad["support_health"] = ""
    original = profile.compile_eval_context

    def _broken(artifact: dict, phase: Phase) -> list:
        items = original(artifact, phase)
        # Strip the support-health item to simulate a regression.
        return [it for it in items if it.metadata.get("role") != profile.ROLE_SUPPORT]

    profile.compile_eval_context = _broken  # type: ignore[assignment]
    try:
        with pytest.raises(AssertionError):
            profile.check_invariants(bad)
    finally:
        profile.compile_eval_context = original  # type: ignore[assignment]


def test_main_runs_and_reports_all_pass() -> None:
    """End-to-end: ``main()`` runs offline and every invariant line is PASS."""
    buf = io.StringIO()
    with redirect_stdout(buf):
        profile.main()
    out = buf.getvalue()
    assert "[FAIL]" not in out
    assert out.count("[PASS]") >= 9  # 3 statuses x >=3 checks
    assert "high_risk: caveats are foregrounded before the estimate" in out

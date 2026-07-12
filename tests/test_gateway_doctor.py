"""Tests for contextweaver.adapters.gateway_doctor (issue #395)."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

from contextweaver.adapters._doctor_checks import find_schema_keys
from contextweaver.adapters.gateway_doctor import (
    CONFIG_KEYS,
    DoctorFinding,
    DoctorReport,
    run_doctor,
)

GOOD_DESCRIPTION = "Search invoices by date range and customer name."


def write_catalog(tmp_path: Path, items: list[dict[str, object]]) -> Path:
    path = tmp_path / "catalog.json"
    path.write_text(json.dumps(items), encoding="utf-8")
    return path


def write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "gateway.yaml"
    path.write_text(text, encoding="utf-8")
    return path


def make_items(n: int = 3, description: str = GOOD_DESCRIPTION) -> list[dict[str, object]]:
    return [
        {
            "id": f"billing.tool{i}",
            "kind": "tool",
            "name": f"tool{i}",
            "description": description,
            "namespace": "billing",
            "args_schema": {"type": "object", "properties": {"q": {"type": "string"}}},
        }
        for i in range(n)
    ]


def levels_by_check(report: DoctorReport) -> dict[str, str]:
    # Last finding wins per check id; fine for these single-finding checks.
    return {f.check: f.level for f in report.findings}


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------


def test_good_static_config_all_ok_exit_zero(tmp_path: Path) -> None:
    write_catalog(tmp_path, make_items())
    config = write_config(tmp_path, "catalog: catalog.json\npolicy_preset: safe\n")
    report = run_doctor(config)
    assert report.counts["fail"] == 0
    assert report.counts["warn"] == 0
    assert report.exit_code() == 0
    checks = levels_by_check(report)
    for check in (
        "config.parse",
        "config.source",
        "config.policy_preset",
        "catalog.load",
        "catalog.references",
        "catalog.metadata",
        "cards.schema_hiding",
        "catalog.hydration",
        "artifacts.writable",
    ):
        assert checks[check] == "ok", check


def test_render_text_uses_check_prefixes(tmp_path: Path) -> None:
    write_catalog(tmp_path, make_items())
    config = write_config(tmp_path, "catalog: catalog.json\n")
    text = run_doctor(config).render_text()
    assert text.startswith("contextweaver gateway doctor\n")
    assert "✓ catalog.load:" in text
    assert text.rstrip().splitlines()[-1].startswith("checks: ok=")


def test_report_to_dict_and_finding_serde(tmp_path: Path) -> None:
    finding = DoctorFinding("config.parse", "warn", "msg", hint="h")
    assert finding.to_dict() == {
        "check": "config.parse",
        "level": "warn",
        "message": "msg",
        "hint": "h",
    }
    write_catalog(tmp_path, make_items())
    payload = run_doctor(write_config(tmp_path, "catalog: catalog.json\n")).to_dict()
    json.dumps(payload)  # JSON-compatible
    assert set(payload) == {"counts", "findings"}


# ---------------------------------------------------------------------------
# Config-level failures and warnings
# ---------------------------------------------------------------------------


def test_malformed_yaml_fails(tmp_path: Path) -> None:
    config = write_config(tmp_path, "catalog: [unclosed\n")
    report = run_doctor(config)
    assert levels_by_check(report)["config.parse"] == "fail"
    assert report.exit_code() == 1


def test_non_mapping_config_fails(tmp_path: Path) -> None:
    config = write_config(tmp_path, "- just\n- a list\n")
    assert levels_by_check(run_doctor(config))["config.parse"] == "fail"


def test_both_catalog_and_upstreams_fails(tmp_path: Path) -> None:
    write_catalog(tmp_path, make_items())
    config = write_config(
        tmp_path,
        "catalog: catalog.json\nupstreams:\n  fs:\n    command: echo\n",
    )
    report = run_doctor(config)
    assert levels_by_check(report)["config.source"] == "fail"
    assert report.exit_code() == 1


def test_neither_catalog_nor_upstreams_fails(tmp_path: Path) -> None:
    config = write_config(tmp_path, "quiet: true\n")
    finding = next(f for f in run_doctor(config).findings if f.check == "config.source")
    assert finding.level == "fail"
    assert "neither" in finding.message


def test_unknown_top_level_key_warns(tmp_path: Path) -> None:
    write_catalog(tmp_path, make_items())
    config = write_config(tmp_path, "catalog: catalog.json\ncatalogg: oops\n")
    report = run_doctor(config)
    finding = next(f for f in report.findings if f.check == "config.keys")
    assert finding.level == "warn"
    assert "catalogg" in finding.message
    assert "catalogg" not in CONFIG_KEYS


def test_bad_value_blocks_fail(tmp_path: Path) -> None:
    write_catalog(tmp_path, make_items())
    config = write_config(
        tmp_path,
        "catalog: catalog.json\npolicy_preset: warp9\nstartup:\n  mode: chaotic\n",
    )
    checks = levels_by_check(run_doctor(config))
    assert checks["config.startup"] == "fail"
    assert checks["config.policy_preset"] == "fail"


# ---------------------------------------------------------------------------
# Catalog checks
# ---------------------------------------------------------------------------


def test_empty_descriptions_warn(tmp_path: Path) -> None:
    write_catalog(tmp_path, make_items(description=""))
    report = run_doctor(write_config(tmp_path, "catalog: catalog.json\n"))
    finding = next(f for f in report.findings if f.check == "catalog.metadata")
    assert finding.level == "warn"
    assert finding.message.startswith("3 item(s)")


def test_duplicate_ids_fail(tmp_path: Path) -> None:
    items = make_items(2)
    items[1]["id"] = items[0]["id"]
    write_catalog(tmp_path, items)
    finding = next(
        f
        for f in run_doctor(write_config(tmp_path, "catalog: catalog.json\n")).findings
        if f.check == "catalog.load"
    )
    assert finding.level == "fail"
    assert "duplicate" in finding.message


def test_empty_catalog_fails(tmp_path: Path) -> None:
    write_catalog(tmp_path, [])
    checks = levels_by_check(run_doctor(write_config(tmp_path, "catalog: catalog.json\n")))
    assert checks["catalog.load"] == "fail"


def test_dangling_reference_warns(tmp_path: Path) -> None:
    items = make_items(2)
    items[0]["depends_on"] = ["billing.missing"]
    write_catalog(tmp_path, items)
    checks = levels_by_check(run_doctor(write_config(tmp_path, "catalog: catalog.json\n")))
    assert checks["catalog.references"] == "warn"


def test_card_walker_detects_leaked_schema_keys() -> None:
    # Called directly on a poisoned payload — the invariant guard itself.
    poisoned = {
        "id": "t1",
        "name": "t1",
        "nested": [{"inputSchema": {"type": "object"}}],
        "args_schema": {"properties": {"q": {}}},
    }
    hits = find_schema_keys(poisoned)
    assert hits == sorted(hits)
    assert "args_schema" in hits
    assert "args_schema.properties" in hits
    assert "nested[0].inputSchema" in hits
    assert find_schema_keys({"id": "t1", "has_schema": True}) == []


def test_smoke_queries_produce_findings(tmp_path: Path) -> None:
    write_catalog(tmp_path, make_items())
    config = write_config(tmp_path, "catalog: catalog.json\n")
    report = run_doctor(config, smoke_queries=["search invoices"])
    smoke = [f for f in report.findings if f.check == "smoke.query"]
    assert len(smoke) == 1
    assert smoke[0].level in ("ok", "warn")
    assert "search invoices" in smoke[0].message


# ---------------------------------------------------------------------------
# Artifact-store writability
# ---------------------------------------------------------------------------


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permissions semantics")
# ``os.geteuid`` is POSIX-only; guard the call so this decorator does not raise
# at collection time on Windows (where the win32 skip above already applies).
@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0, reason="root bypasses permission bits"
)
def test_unwritable_state_dir_fails(tmp_path: Path) -> None:
    locked = tmp_path / "locked"
    locked.mkdir()
    locked.chmod(0o500)
    write_catalog(tmp_path, make_items())
    config = write_config(tmp_path, "catalog: catalog.json\nstate_dir: locked/state\n")
    try:
        checks = levels_by_check(run_doctor(config))
    finally:
        locked.chmod(0o700)
    assert checks["artifacts.writable"] == "fail"


def test_state_dir_blocked_by_file_fails(tmp_path: Path) -> None:
    # A regular file where the state dir should be fails even when running
    # as root (unlike the chmod probe above).
    (tmp_path / "state").write_text("i am a file", encoding="utf-8")
    write_catalog(tmp_path, make_items())
    config = write_config(tmp_path, "catalog: catalog.json\nstate_dir: state\n")
    checks = levels_by_check(run_doctor(config))
    assert checks["artifacts.writable"] == "fail"


def test_tempdir_probe_when_no_state_dir_is_cleaned_up(tmp_path: Path) -> None:
    write_catalog(tmp_path, make_items())
    report = run_doctor(write_config(tmp_path, "catalog: catalog.json\n"))
    finding = next(f for f in report.findings if f.check == "artifacts.writable")
    assert finding.level == "ok"
    assert "tempdir probe" in finding.message


# ---------------------------------------------------------------------------
# Exit codes and extras
# ---------------------------------------------------------------------------


def test_strict_exit_code_promotes_warnings(tmp_path: Path) -> None:
    write_catalog(tmp_path, make_items())
    config = write_config(tmp_path, "catalog: catalog.json\nmystery_key: 1\n")
    report = run_doctor(config)
    assert report.counts["fail"] == 0
    assert report.counts["warn"] >= 1
    assert report.exit_code() == 0
    assert report.exit_code(strict=True) == 1


def test_extras_findings_are_informational_never_fail(tmp_path: Path) -> None:
    write_catalog(tmp_path, make_items())
    report = run_doctor(write_config(tmp_path, "catalog: catalog.json\n"))
    extras = [f for f in report.findings if f.check.startswith("extras.")]
    assert len(extras) == 4
    assert all(f.level == "ok" for f in extras)
    for finding in extras:
        if "not installed" in finding.message:
            assert finding.hint is not None and "pip install" in finding.hint


def test_live_probe_reports_failure_as_finding(tmp_path: Path) -> None:
    config = write_config(
        tmp_path,
        "upstreams:\n  ghost:\n    command: /nonexistent/definitely-missing-binary\n"
        "startup:\n  upstream_timeout_seconds: 1\n",
    )
    report = run_doctor(config, live=True)
    finding = next(f for f in report.findings if f.check == "upstreams.live")
    assert finding.level == "fail"
    assert report.exit_code() == 1

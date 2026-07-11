"""Gateway preflight doctor for ``mcp doctor`` (issue #395).

Pure-library preflight over an ``mcp serve`` config: every check appends a
:class:`DoctorFinding` (``ok`` / ``warn`` / ``fail``) instead of raising, so
one :func:`run_doctor` run reports *all* problems at once.  The coordinator
wires this into the ``contextweaver mcp doctor`` CLI; this module must not
import typer.

Checked: config parse + accepted key set (:data:`CONFIG_KEYS`), catalog XOR
upstreams, value blocks (``startup`` / ``artifacts`` / ``policy_preset``),
static catalog load (duplicate ids, dangling references, weak metadata),
ChoiceCard schema-hiding, hydration, artifact-store writability,
optional-extras availability, and — opt-in — live upstream launch and routing
smoke queries.  Check implementations live in the private sibling
:mod:`contextweaver.adapters._doctor_checks` (size-ceiling split, mirroring
``_proxy_dispatch.py``).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Any

from contextweaver.adapters import _doctor_checks as _checks
from contextweaver.adapters._doctor_checks import CONFIG_KEYS, DoctorFinding

if TYPE_CHECKING:
    from contextweaver.types import SelectableItem

_LEVEL_PREFIX: dict[str, str] = {"ok": "✓", "warn": "!", "fail": "✗"}


@dataclass
class DoctorReport:
    """Aggregate result of a :func:`run_doctor` run.

    Attributes:
        findings: Every :class:`DoctorFinding`, in deterministic check order.
    """

    findings: list[DoctorFinding] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        """Per-level finding counts (always includes all three levels)."""
        tally = Counter(f.level for f in self.findings)
        return {"ok": tally["ok"], "warn": tally["warn"], "fail": tally["fail"]}

    def exit_code(self, strict: bool = False) -> int:
        """Return the CLI exit code: 1 on any ``fail`` (or ``warn`` if *strict*)."""
        counts = self.counts
        return 1 if counts["fail"] or (strict and counts["warn"]) else 0

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {"counts": self.counts, "findings": [f.to_dict() for f in self.findings]}

    def render_text(self) -> str:
        """Render the report as deterministic one-line-per-finding text (✓/!/✗)."""
        lines = ["contextweaver gateway doctor"]
        for finding in self.findings:
            hint = f" (hint: {finding.hint})" if finding.hint else ""
            lines.append(f"{_LEVEL_PREFIX[finding.level]} {finding.check}: {finding.message}{hint}")
        counts = self.counts
        lines.append(f"checks: ok={counts['ok']} warn={counts['warn']} fail={counts['fail']}")
        return "\n".join(lines) + "\n"


def run_doctor(
    config_path: str | Path, *, live: bool = False, smoke_queries: list[str] | None = None
) -> DoctorReport:
    """Run every preflight check over an ``mcp serve`` config (issue #395).

    Checks never raise: each outcome lands as a :class:`DoctorFinding`, so one
    run surfaces every problem.  Deterministic for the same inputs/environment.

    Args:
        config_path: Path to the JSON/YAML ``mcp serve`` config file.
        live: When ``True`` and the config declares ``upstreams``, launch them
            briefly (short per-upstream timeout) and report the outcome.
        smoke_queries: Optional routing smoke queries, each routed through a
            default Router over the static catalog; zero candidates warns.

    Returns:
        A :class:`DoctorReport`; use :meth:`DoctorReport.exit_code` for CLI
        gating and :meth:`DoctorReport.render_text` for display.
    """
    findings: list[DoctorFinding] = []
    path = Path(config_path)
    cfg = _checks.parse_config(path, findings)
    items: list[SelectableItem] = []
    if cfg is not None:
        _checks.check_blocks(cfg, findings)
        if "catalog" in cfg:
            items = _checks.load_items(cfg, path.parent, findings)
        if items:
            _checks.check_catalog_quality(items, findings)
        _checks.check_artifact_store(cfg, path.parent, findings)
        if live and "upstreams" in cfg:
            _checks.check_live(cfg, findings)
        if smoke_queries and items:
            _checks.check_smoke(items, list(smoke_queries), findings)
    _checks.check_extras(findings)
    return DoctorReport(findings=findings)


__all__ = ["CONFIG_KEYS", "DoctorFinding", "DoctorReport", "run_doctor"]

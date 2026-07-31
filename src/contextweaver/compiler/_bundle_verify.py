"""Trust-projection recomputation for compiled-bundle verification."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

from contextweaver.compiler._bundle_trust import summarize_trust_inputs
from contextweaver.compiler.resources import ResourceDescriptor
from contextweaver.compiler.sources import CapabilitySourceSnapshot
from contextweaver.compiler.trust import TrustSummary
from contextweaver.exceptions import ValidationError


def recomputed_trust_findings(bundle_path: Path, trust: TrustSummary) -> list[str]:
    """Recompute the trust projection from on-disk artifacts and compare.

    ``TrustSummary`` is a recomputable projection, so ``manifest.json`` (which is
    not covered by the component digests) is treated as an untrusted cache: a
    tampered status, warnings, or findings must not survive verification.
    """
    try:
        resources = [
            ResourceDescriptor.from_dict(dict(raw))
            for raw in cast(list[Any], _read_json(bundle_path / "resources.json"))
        ]
        lock = cast(dict[str, Any], _read_json(bundle_path / "lock.json"))
        sources = [CapabilitySourceSnapshot.from_dict(dict(raw)) for raw in lock.get("sources", [])]
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError, ValidationError) as exc:
        return [f"cannot recompute trust projection: {exc}"]
    status, warnings, findings = summarize_trust_inputs(sources, resources)
    mismatches: list[str] = []
    if trust.status != status:
        mismatches.append("trust status does not match recomputed trust projection")
    if list(trust.warnings) != warnings:
        mismatches.append("trust warnings do not match recomputed trust projection")
    if list(trust.findings) != findings:
        mismatches.append("trust findings do not match recomputed trust projection")
    return mismatches


def _read_json(path: Path) -> object:
    return json.loads(path.read_text(encoding="utf-8"))

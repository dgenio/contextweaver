"""Agent-safe context profile for statistical evaluation artifacts (#335).

Offline policy-evaluation reports are large and easy to misread. An agent
that sees only a headline score such as ``V_hat`` can draw a confident,
wrong conclusion. This profile compiles an evaluation artifact into
**phase-aware, agent-safe** context that foregrounds support health,
uncertainty, and caveats, and only then the headline estimate.

The profile is the function :func:`compile_eval_context`. It enforces two
safety invariants for every artifact:

1. **Never present ``V_hat`` without support diagnostics.** Any phase whose
   output contains the value estimate also contains a support-health item
   *earlier* in the list. The route phase never exposes the estimate at all.
2. **High-risk artifacts foreground caveats before estimates.** For a
   ``high_risk`` artifact, warnings and limitations are emitted before the
   value estimate.

Three fixtures (``ok`` / ``caution`` / ``high_risk``) under ``fixtures/``
exercise the profile. Everything is deterministic and offline — no model,
no network, no statistical computation (that belongs to the artifact
producer, e.g. ``skdr-eval``; this example only *shapes* its output).

Run standalone::

    python examples/architectures/eval_artifact_profile/main.py

Or via ``make example`` (the ``architectures`` umbrella target).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from contextweaver.context.prompt import render_context
from contextweaver.types import ContextItem, ItemKind, Phase

_FIXTURES = Path(__file__).parent / "fixtures"

# Roles stamped on each compiled item so the safety invariants can be
# checked structurally (rather than by string-matching the rendered text).
ROLE_SUPPORT = "support_health"
ROLE_VALUE = "value_estimate"
ROLE_CAVEAT = "caveat"

# Below this effective sample size an offline estimate is treated as
# unreliable; surfaced here only to document the producer's convention.
RELIABILITY_FLOOR = 500

_USABILITY = {
    "ok": "yes — the evidence is usable",
    "caution": "only with caution — the evidence is weak",
    "high_risk": "no — the evidence is not usable for a decision",
}
_DECISION = {
    "ok": "adopt the candidate (delta is positive and stable)",
    "caution": "do not adopt yet — gather more support before deciding",
    "high_risk": "do not act on this estimate — treat the run as inconclusive",
}


def _item(seq: int, kind: ItemKind, text: str, role: str) -> ContextItem:
    """Build a compiled context item tagged with its safety *role*."""
    return ContextItem(id=f"eval-{role}-{seq}", kind=kind, text=text, metadata={"role": role})


def compile_eval_context(artifact: dict[str, Any], phase: Phase) -> list[ContextItem]:
    """Compile an evaluation *artifact* into agent-safe context for *phase*.

    Args:
        artifact: A decoded evaluation-artifact dict (see ``fixtures/``).
        phase: The pipeline phase the context is being built for.

    Returns:
        An ordered list of :class:`~contextweaver.types.ContextItem`. The
        ordering encodes the safety invariants documented in the module
        docstring.
    """
    status = str(artifact["decision_status"])
    items: list[ContextItem] = []
    seq = 0

    if phase is Phase.route:
        # Route: summary metadata only — no metrics, no estimate.
        diagnostics = ", ".join(artifact.get("available_diagnostics", []))
        items.append(
            _item(
                seq,
                ItemKind.doc_snippet,
                f"evaluation artifact ({artifact['artifact_type']}): status={status}; "
                f"diagnostics=[{diagnostics}]; needs_interpretation="
                f"{artifact['needs_interpretation']}; policy_gating={artifact['policy_gating']}",
                "summary_metadata",
            )
        )
        return items

    if phase is Phase.interpret:
        # 1. Support health is ALWAYS first — it gates how every later
        #    number should be read.
        items.append(
            _item(
                seq,
                ItemKind.memory_fact,
                f"support health: {artifact['support_health']}",
                ROLE_SUPPORT,
            )
        )
        seq += 1
        # 2. Warnings (caveats) come next.
        for warning in artifact.get("warnings", []):
            items.append(_item(seq, ItemKind.policy, f"warning: {warning}", ROLE_CAVEAT))
            seq += 1
        # 3. For high-risk artifacts, surface limitations BEFORE any estimate.
        if status == "high_risk":
            for limitation in artifact.get("limitations", []):
                items.append(_item(seq, ItemKind.policy, f"limitation: {limitation}", ROLE_CAVEAT))
                seq += 1
        # 4. Assumptions the estimate rests on.
        for assumption in artifact.get("assumptions", []):
            items.append(_item(seq, ItemKind.policy, f"assumption: {assumption}", "assumption"))
            seq += 1
        # 5. Effect size with uncertainty.
        metrics = artifact["key_metrics"]
        items.append(
            _item(
                seq,
                ItemKind.memory_fact,
                f"delta (candidate - baseline): {metrics['delta']:+.3f} "
                f"(95% CI {metrics['delta_ci95']}); baseline={metrics['baseline_value']}, "
                f"candidate={metrics['candidate_value']}",
                "metrics",
            )
        )
        seq += 1
        # 6. Sensitivity / decision stability.
        items.append(
            _item(
                seq,
                ItemKind.memory_fact,
                f"decision stability: {artifact['decision_stability']}",
                "stability",
            )
        )
        seq += 1
        # 7. Limitations (for non-high-risk; high-risk already surfaced them).
        if status != "high_risk":
            for limitation in artifact.get("limitations", []):
                items.append(_item(seq, ItemKind.policy, f"limitation: {limitation}", ROLE_CAVEAT))
                seq += 1
        # 8. Headline estimate LAST — never before its support diagnostics.
        estimate = artifact["value_estimate"]
        items.append(
            _item(
                seq,
                ItemKind.memory_fact,
                f"headline estimate {estimate['name']}={estimate['candidate']} "
                f"(95% CI {estimate['ci95']}) — read only alongside the support and "
                "uncertainty above",
                ROLE_VALUE,
            )
        )
        seq += 1
        # 9. Handle to the full artifact for drilldown.
        items.append(
            _item(
                seq,
                ItemKind.doc_snippet,
                f"full artifact: {artifact['full_artifact_handle']}",
                "handle",
            )
        )
        return items

    # Phase.answer (and any other phase): a compact human-safe summary.
    items.append(_item(seq, ItemKind.doc_snippet, f"evaluated: {artifact['evaluated']}", "summary"))
    seq += 1
    items.append(
        _item(
            seq,
            ItemKind.memory_fact,
            f"evidence usable? {_USABILITY[status]} ({artifact['support_health']})",
            ROLE_SUPPORT,
        )
    )
    seq += 1
    caveats = list(artifact.get("warnings", [])) or ["none beyond the stated limitations"]
    items.append(_item(seq, ItemKind.policy, "caveats: " + "; ".join(caveats), ROLE_CAVEAT))
    seq += 1
    items.append(
        _item(seq, ItemKind.doc_snippet, f"decision supported: {_DECISION[status]}", "decision")
    )
    seq += 1
    # Only an OK artifact leads the human-facing summary with the estimate,
    # and only after the usability statement above.
    if status == "ok":
        estimate = artifact["value_estimate"]
        items.append(
            _item(
                seq,
                ItemKind.memory_fact,
                f"headline {estimate['name']}={estimate['candidate']} (95% CI {estimate['ci95']})",
                ROLE_VALUE,
            )
        )
    return items


def check_invariants(artifact: dict[str, Any]) -> list[str]:
    """Return human-readable PASS/FAIL lines for the profile's safety rules.

    Raises:
        AssertionError: if any safety invariant is violated, so a
            regression breaks ``make example`` rather than shipping unsafe
            context shaping silently.
    """
    lines: list[str] = []

    def _roles(items: list[ContextItem]) -> list[str]:
        return [str(it.metadata.get("role", "")) for it in items]

    route_roles = _roles(compile_eval_context(artifact, Phase.route))
    assert ROLE_VALUE not in route_roles, "route phase must not expose the value estimate"
    lines.append("  [PASS] route phase exposes no headline estimate")

    for phase in (Phase.interpret, Phase.answer):
        roles = _roles(compile_eval_context(artifact, phase))
        if ROLE_VALUE in roles:
            assert ROLE_SUPPORT in roles, f"{phase.value}: estimate shown without support health"
            assert roles.index(ROLE_SUPPORT) < roles.index(ROLE_VALUE), (
                f"{phase.value}: support health must precede the estimate"
            )
            lines.append(f"  [PASS] {phase.value} phase: support health precedes the estimate")
        else:
            lines.append(f"  [PASS] {phase.value} phase: estimate withheld (safe for this status)")

    if artifact["decision_status"] == "high_risk":
        roles = _roles(compile_eval_context(artifact, Phase.interpret))
        assert ROLE_VALUE in roles and ROLE_CAVEAT in roles
        assert roles.index(ROLE_CAVEAT) < roles.index(ROLE_VALUE), (
            "high_risk: caveats must be foregrounded before the estimate"
        )
        lines.append("  [PASS] high_risk: caveats are foregrounded before the estimate")

    return lines


def _print_header(title: str) -> None:
    """Print a section banner consistent with the other architecture scripts."""
    print()
    print("=" * 76)
    print(title)
    print("=" * 76)


def main() -> None:
    """Compile and validate all three artifact fixtures through every phase."""
    _print_header("contextweaver -- Agent-safe evaluation-artifact context profile")
    print(f"Reliability floor (effective sample size): {RELIABILITY_FLOOR}")

    for status in ("ok", "caution", "high_risk"):
        artifact = json.loads((_FIXTURES / f"artifact_{status}.json").read_text(encoding="utf-8"))
        _print_header(f"Artifact: {status}  —  {artifact['evaluated']}")

        for phase in (Phase.route, Phase.interpret, Phase.answer):
            items = compile_eval_context(artifact, phase)
            print(f"\n--- {phase.value} phase ({len(items)} items) ---")
            print(render_context(items))

        print("\n--- safety invariants ---")
        for line in check_invariants(artifact):
            print(line)


if __name__ == "__main__":
    main()

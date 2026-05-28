# Agent-safe evaluation-artifact context profile — captured run

Output of `python examples/architectures/eval_artifact_profile/main.py`
from a clean checkout. Deterministic and offline: the three artifact
fixtures are checked in under `fixtures/`, and the profile does no
statistical computation — it only shapes the artifact into phase-aware,
agent-safe context. The trailing `[PASS]` lines are the runtime safety
invariant checks.

```

============================================================================
contextweaver -- Agent-safe evaluation-artifact context profile
============================================================================
Reliability floor (effective sample size): 500

============================================================================
Artifact: ok  —  candidate ranking policy v2 vs production baseline v1
============================================================================

--- route phase (1 items) ---
[DOCUMENT]
evaluation artifact (offline_policy_evaluation): status=ok; diagnostics=[support, uncertainty, sensitivity, overlap]; needs_interpretation=True; policy_gating=False

--- interpret phase (8 items) ---
[FACT]
support health: healthy: effective sample size 18,432; action overlap good; no extrapolation needed

[POLICY]
assumption: logged propensities are correct

[POLICY]
assumption: no unobserved confounding in the logging policy

[FACT]
delta (candidate - baseline): +0.035 (95% CI [0.011, 0.059]); baseline=0.412, candidate=0.447

[FACT]
decision stability: stable across sensitivity sweeps (sign of delta unchanged)

[POLICY]
limitation: evaluation window covers 14 days of traffic only

[FACT]
headline estimate V_hat=0.447 (95% CI [0.421, 0.473]) — read only alongside the support and uncertainty above

[DOCUMENT]
full artifact: artifact:ope:ope-2026-05-20-abc

--- answer phase (5 items) ---
[DOCUMENT]
evaluated: candidate ranking policy v2 vs production baseline v1

[FACT]
evidence usable? yes — the evidence is usable (healthy: effective sample size 18,432; action overlap good; no extrapolation needed)

[POLICY]
caveats: none beyond the stated limitations

[DOCUMENT]
decision supported: adopt the candidate (delta is positive and stable)

[FACT]
headline V_hat=0.447 (95% CI [0.421, 0.473])

--- safety invariants ---
  [PASS] route phase exposes no headline estimate
  [PASS] interpret phase: support health precedes the estimate
  [PASS] answer phase: support health precedes the estimate

============================================================================
Artifact: caution  —  candidate pricing policy v5 vs production baseline v4
============================================================================

--- route phase (1 items) ---
[DOCUMENT]
evaluation artifact (offline_policy_evaluation): status=caution; diagnostics=[support, uncertainty, sensitivity, overlap]; needs_interpretation=True; policy_gating=True

--- interpret phase (11 items) ---
[FACT]
support health: moderate: effective sample size 2,140; partial action overlap on high-value segment

[POLICY]
warning: confidence interval for the delta crosses zero

[POLICY]
warning: high-value customer segment is thinly supported

[POLICY]
assumption: logged propensities are correct

[POLICY]
assumption: segment membership is stable over the window

[FACT]
delta (candidate - baseline): +0.021 (95% CI [-0.006, 0.048]); baseline=0.318, candidate=0.339

[FACT]
decision stability: unstable: sign of delta flips under the pessimistic sensitivity sweep

[POLICY]
limitation: evaluation window covers 7 days only

[POLICY]
limitation: no holiday traffic represented

[FACT]
headline estimate V_hat=0.339 (95% CI [0.291, 0.387]) — read only alongside the support and uncertainty above

[DOCUMENT]
full artifact: artifact:ope:ope-2026-05-22-def

--- answer phase (4 items) ---
[DOCUMENT]
evaluated: candidate pricing policy v5 vs production baseline v4

[FACT]
evidence usable? only with caution — the evidence is weak (moderate: effective sample size 2,140; partial action overlap on high-value segment)

[POLICY]
caveats: confidence interval for the delta crosses zero; high-value customer segment is thinly supported

[DOCUMENT]
decision supported: do not adopt yet — gather more support before deciding

--- safety invariants ---
  [PASS] route phase exposes no headline estimate
  [PASS] interpret phase: support health precedes the estimate
  [PASS] answer phase: estimate withheld (safe for this status)

============================================================================
Artifact: high_risk  —  candidate aggressive-retention policy v9 vs production baseline v8
============================================================================

--- route phase (1 items) ---
[DOCUMENT]
evaluation artifact (offline_policy_evaluation): status=high_risk; diagnostics=[support, uncertainty, overlap]; needs_interpretation=True; policy_gating=True

--- interpret phase (13 items) ---
[FACT]
support health: poor: effective sample size 184; severe action mismatch; heavy extrapolation

[POLICY]
warning: estimate relies on heavy extrapolation outside the logged action distribution

[POLICY]
warning: effective sample size is below the reliability floor (184 < 500)

[POLICY]
warning: importance weights are extreme (max weight 1,920)

[POLICY]
limitation: evaluation window covers 3 days only

[POLICY]
limitation: no sensitivity sweep available for this run

[POLICY]
limitation: candidate policy visits actions never taken by the logging policy

[POLICY]
assumption: logged propensities are correct

[POLICY]
assumption: the model extrapolates correctly to unsupported actions (UNVERIFIED)

[FACT]
delta (candidate - baseline): +0.283 (95% CI [-0.14, 0.706]); baseline=0.205, candidate=0.488

[FACT]
decision stability: not assessable: insufficient support to run a meaningful sensitivity sweep

[FACT]
headline estimate V_hat=0.488 (95% CI [0.061, 0.915]) — read only alongside the support and uncertainty above

[DOCUMENT]
full artifact: artifact:ope:ope-2026-05-25-ghi

--- answer phase (4 items) ---
[DOCUMENT]
evaluated: candidate aggressive-retention policy v9 vs production baseline v8

[FACT]
evidence usable? no — the evidence is not usable for a decision (poor: effective sample size 184; severe action mismatch; heavy extrapolation)

[POLICY]
caveats: estimate relies on heavy extrapolation outside the logged action distribution; effective sample size is below the reliability floor (184 < 500); importance weights are extreme (max weight 1,920)

[DOCUMENT]
decision supported: do not act on this estimate — treat the run as inconclusive

--- safety invariants ---
  [PASS] route phase exposes no headline estimate
  [PASS] interpret phase: support health precedes the estimate
  [PASS] answer phase: estimate withheld (safe for this status)
  [PASS] high_risk: caveats are foregrounded before the estimate
```

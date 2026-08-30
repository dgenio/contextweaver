# Claims and evidence

ContextWeaver treats public claims as versioned engineering artifacts, not marketing constants.

This page records what the project can currently support, what is true only for a committed fixture, what remains unverified, and what it deliberately does **not** claim. Quantitative results should always be read with the linked benchmark configuration and measurement method.

## Status meanings

- **Proven** — supported by a reproducible artifact for the stated scope.
- **Fixture-bound** — reproduced on a committed deterministic scenario, but not evidence of universal or production benefit.
- **Unverified** — plausible/product hypothesis; current evidence is not sufficient for a public claim.
- **Rejected / non-claim** — contradicted, too broad, or explicitly outside the product boundary.

## Current claim registry

| Claim | Status | Scope / baseline | Evidence | Important caveat |
|---|---|---|---|---|
| ContextWeaver can produce deterministic bounded routing/context artifacts for supported deterministic inputs/configuration. | **Proven** | In-tree deterministic paths covered by tests/fixtures. | CI, deterministic benchmark fixtures, bundle/runtime tests. | Does not imply a model/provider response is deterministic. |
| The committed naive-concat scenarios use less model-visible context with ContextWeaver. | **Fixture-bound** | Historical naive all-tools/all-history baseline. From snapshot schema v2 onward both arms use `heuristic/chardiv4` and the unit is **estimated tokens**. | `benchmarks/results/history/`, `benchmarks/trend.md`, `scripts/baseline_naive.py`. | Schema-v1 release snapshots (0.16–0.18) predate enforced estimator identity and are retained as legacy/unverified methodology. The naive baseline is not a strong modern competitor. |
| ContextWeaver improves answer quality. | **Unverified** | Requires real-model/task-success comparison. | Tracked by #445. | Fewer/cleaner context tokens do not prove better answers. |
| ContextWeaver is better than provider-native tool search/deferred loading. | **Unverified** | Must compare current provider-native mechanisms, simple retrieval, ContextWeaver and combined approaches. | Tracked by #445. | Native tool search may be the simpler/better choice for many users. |
| The full compiler workflow solves a painful capability-surface reproducibility/drift/evaluation problem for independent teams. | **Unverified** | External product validation. | #758, #840, #658, #551. | Architecture existing in the repository is not evidence of user demand. |
| Versioned bundles/locks, source provenance, diff/drift and pre-promotion evaluation can make a supported capability surface inspectable/reproducible. | **Fixture-bound / emerging** | Only the shipped/maintained compiler surfaces and sources demonstrated by current tests. | Compiler tests, #434, #409 and related artifact/runtime work. | Each subclaim must graduate independently; do not treat the whole compiler thesis as proven. |
| Artifact-backed result handling is superior to modern provider/runtime-native programmatic tool handling. | **Unverified** | Requires result-handling ablation. | #445. | Potential value is containment/provenance/replay/provider independence, not token reduction alone. |
| ContextWeaver is a production IAM, authz, execution or orchestration control plane. | **Rejected / non-claim** | Outside product boundary. | #758, architecture docs. | The host/runtime owns credentials, authorization, execution, retries and side effects. |
| Every agent with many tools should use ContextWeaver. | **Rejected / non-claim** | Not supported. | `docs/comparison.md`, #433. | Small static tool sets or provider-native search may require no additional layer. |
| Stars, forks or downloads demonstrate adoption. | **Rejected / non-claim** | Product/community evidence policy. | #551, #658. | Adoption means genuine external use; retention is stronger evidence than a trial. |

## Token-reduction methodology boundary

### Schema v2 and later

The deterministic release-history comparison uses the same estimator for both sides of the ratio:

```text
naive estimated tokens        ┐
                              ├─ heuristic/chardiv4
ContextWeaver estimated tokens┘
```

The estimator identifier is stored in each `naive_delta` and in the release snapshot. Snapshot creation fails if measurement metadata is absent or inconsistent.

These values are **estimated prompt-token units**, not provider billing tokens. Exact tokenizer/provider accounting belongs in a separate benchmark where both arms use the same tokenizer and its identity/version are recorded.

### Schema v1 history (0.16–0.18)

During the v0.18.1 release recovery, #841 discovered that the naive arm could use `cl100k_base` when tiktoken was available while the ContextWeaver arm used `CharDivFourEstimator`; the naive arm also silently fell back to `len//4`. That made the ratio method-dependent on the environment.

The old rows remain in `benchmarks/trend.md` for historical transparency, but their token-reduction values are labelled **legacy/unverified methodology** and must not be used as a continuous quantitative trend against schema-v2 measurements.

## What should appear in public launch material

Before reusing a claim in the README, a launch post or an external integration listing:

1. name the baseline;
2. identify the exact fixture/dataset and ContextWeaver version;
3. use the same measurement unit across compared arms;
4. link the reproducer/result artifact;
5. state the important limitation or simpler alternative;
6. if an external provider/runtime is involved, record the model/API feature and evaluation date;
7. preserve negative results and cases where ContextWeaver loses.

Broad product-distribution work remains gated by #445 (modern comparative evidence), #434 (maintained product proof), and #658/#840 (unassisted/external validation).

## Where the evidence should move next

The decisive next benchmark is not ContextWeaver versus dumping every schema into a prompt. #445 requires direct comparison among:

- naive all-tools/full-result control;
- provider-native tool search/deferred loading;
- simple client-side retrieval;
- ContextWeaver routing only;
- the full compiled/runtime path;
- ContextWeaver combined with provider-native mechanisms.

The report must include task success, capability/argument correctness, false exclusion, ambiguity behavior, tokens, latency and cost, plus an explicit section showing where ContextWeaver lost.

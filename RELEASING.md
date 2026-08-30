# Releasing contextweaver

GitHub Releases, PyPI, the MCP Registry and committed release evidence must describe the same product version. Release workflows fail closed; never weaken an integrity gate to make publication pass.

## Normal release path

### 1. Prepare a release branch

Start from current `main` and create `release/vX.Y.Z`.

Add a short-lived `.release-target.json` describing the target version/date, roadmap highlight and release-specific changelog bullets. Pushing that file triggers `.github/workflows/prepare-release.yml`.

The preparation workflow:

1. installs the release candidate;
2. runs the deterministic benchmark into an isolated temporary result file;
3. updates package/version metadata, README current-version markers, CHANGELOG and citation/registry metadata;
4. captures `benchmarks/results/history/X.Y.Z.json` from the isolated result;
5. regenerates `benchmarks/trend.md` and generated LLM documentation;
6. verifies README/security/version/release-evidence drift guards;
7. restores the canonical `benchmarks/results/latest.json` from `main` so release bookkeeping cannot overwrite the normal benchmark artifact;
8. removes the short-lived target file and commits the complete prepared release state.

Review the resulting release snapshot. A changed benchmark number is evidence to investigate, not generated ceremony. Snapshot schema v2 records the token-reduction estimator; schema-v1 token history is intentionally marked legacy/unverified methodology after #841 found mixed estimators in the historical naive comparison.

### 2. Open the release PR

Open `release/vX.Y.Z → main` and let the normal repository checks run. The release PR should contain the complete, reviewable state that will become immutable:

- package version;
- README/version metadata;
- `server.json` and `CITATION.cff`;
- changelog;
- version-specific benchmark snapshot and trend;
- any release-process fixes intended for that version.

**Required checks must be green on the exact PR head that will be merged.** Commits created by a workflow using `GITHUB_TOKEN` do not recursively start new workflows. If automated preparation/formatting produces the final commit, a maintainer-reviewed repository commit or explicit GitHub approval/run is required so the exact prepared head receives the normal PR checks. An earlier green SHA is not sufficient evidence for a later bot-authored SHA.

Do not create a tag or GitHub Release while this PR is red, while the exact-head checks have not run, or while the release evidence is unexplained.

### 3. Merge the prepared release

When the release PR is green and reviewed, merge it normally.

A `pyproject.toml` version change on `main` triggers `.github/workflows/create-release.yml`. That workflow:

1. reruns release-readiness/version/evidence checks at the exact `main` commit;
2. refuses to continue if `vX.Y.Z` already exists — public tags are immutable;
3. creates the GitHub Release/tag at the validated commit;
4. explicitly dispatches `.github/workflows/publish.yml` on that immutable tag.

The explicit dispatch is intentional: GitHub suppresses ordinary workflow recursion for releases created by `GITHUB_TOKEN`, while `workflow_dispatch` is the supported audited hand-off.

### 4. Trusted publish

`Publish to PyPI and MCP Registry` accepts either a human `release: published` event or an explicit `workflow_dispatch` on `vX.Y.Z`. In both cases it first proves the checked-out ref is the package's immutable release tag and revalidates:

- tag ↔ package version;
- version metadata;
- current release snapshot schema/method + trend;
- gating test suite;
- built distribution metadata.

Only then does it publish to PyPI through Trusted Publishing/OIDC and attach provenance attestations. MCP Registry publication runs after PyPI and retries briefly while PyPI metadata propagates.

## Manual preparation fallback

If the preparation workflow is unavailable, reproduce the same invariants manually rather than skipping them.

Generate release evidence to a temporary path so canonical `latest.json` is not overwritten:

```bash
python3 benchmarks/benchmark.py --output /tmp/contextweaver-release-benchmark.json
python3 scripts/render_trend.py --snapshot X.Y.Z \
    --from /tmp/contextweaver-release-benchmark.json
python3 scripts/render_trend.py
python3 scripts/gen_llms.py
python3 scripts/check_release_readiness.py
```

Then run the normal repository validation and open a release PR. Manual tagging should be an exception; prefer the reviewed automated main→release hand-off above.

## Post-publish verification

Verify the canonical user path from a clean environment, not an editable checkout:

```bash
python3 -m pip install --no-cache-dir contextweaver
python3 -c "import contextweaver; print(contextweaver.__version__)"
contextweaver demo --scenario killer
```

Confirm that the printed version matches the GitHub Release. Verify PyPI and MCP Registry outcomes independently; a registry failure must not obscure whether package publication succeeded.

## Recovery from a failed publish

A GitHub Release can exist even when publishing fails. That happened for v0.18.0: its required release benchmark snapshot was missing, so the integrity gate correctly skipped PyPI and the MCP Registry.

When this happens:

- diagnose and fix the violated invariant;
- do not force-move a public tag;
- do not fabricate, copy or rename evidence merely to satisfy a gate;
- if the published release/tag cannot be recovered safely, prepare the smallest patch release from current validated `main`;
- rerun the full release-readiness path;
- verify PyPI explicitly after recovery.

During the v0.18.1 recovery, #841 also demonstrated why release evidence itself must be challenged: the old naive token-reduction ratio mixed `CharDivFourEstimator` for ContextWeaver with `cl100k_base` or a silent fallback on the baseline. The repaired snapshot schema records one estimator for both arms and treats older history as a methodology boundary rather than a continuous trend.

See #837/#839 for the v0.18.0 publication incident and #841 for the evidence-integrity repair.

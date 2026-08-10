# Releasing contextweaver

GitHub Releases, PyPI, the MCP Registry and the committed release evidence must describe the same product version.

The release workflow intentionally fails closed. Do not weaken a failing integrity gate to make a release publish.

## Release preparation

Prepare the release in a normal pull request **before** creating or publishing the GitHub Release.

1. Finish the intended release changes and ensure normal CI is green.
2. Set `[project].version` in `pyproject.toml` to the release version.
3. Update all version-bearing metadata required by the drift guards:
   - README current/comparison/roadmap references;
   - `server.json`;
   - `CITATION.cff` version and release date;
   - SECURITY/version references where required by the current policy.
4. Finalize the CHANGELOG section for the release.
5. Refresh the deterministic benchmark from the release candidate code:

   ```bash
   make benchmark
   python scripts/render_trend.py --snapshot X.Y.Z \
       --from benchmarks/results/latest.json
   make trend
   ```

   Review the new `benchmarks/results/history/X.Y.Z.json` instead of treating it as generated ceremony. A regression or surprising change should be explained before release.
6. Run the explicit release-readiness guard:

   ```bash
   python scripts/check_release_readiness.py
   ```

   It must confirm that the current package version has a matching benchmark-history snapshot and that `benchmarks/trend.md` is synchronized.
7. Run the normal project validation required for the release PR and merge only when required checks are green.

The `Release readiness` workflow runs on pull requests and `main` as an early guard for the same invariant.

## Publish

After the release-preparation PR is merged:

1. Create/tag `vX.Y.Z` from the exact prepared commit.
2. Create and publish the GitHub Release for that tag.
3. The `Publish to PyPI and MCP Registry` workflow independently verifies:
   - tag ↔ package version;
   - version metadata;
   - release benchmark snapshot + trend;
   - test suite;
   - built distribution metadata.
4. Only after verification does the workflow publish to PyPI via Trusted Publishing/OIDC.
5. The MCP Registry publish happens after PyPI because its package metadata depends on the PyPI release.

## Post-publish verification

Verify the canonical user path from a clean environment, not an editable checkout:

```bash
python -m pip install --no-cache-dir contextweaver
python -c "import contextweaver; print(contextweaver.__version__)"
contextweaver demo --scenario killer
```

Confirm that the printed version matches the GitHub Release and that the MCP Registry entry, when applicable, references the same package version.

## Recovery from a failed publish

A GitHub Release can exist even when the release-triggered publish workflow fails. That happened for v0.18.0: the required `benchmarks/results/history/0.18.0.json` had not been committed, so the integrity gate correctly skipped PyPI and the MCP Registry.

When this happens:

- diagnose and fix the violated invariant;
- do not force-move a public tag merely to make the workflow rerun;
- do not fabricate or rename an evidence artifact to satisfy a gate;
- prefer a small patch release when the original immutable release cannot be recovered safely;
- verify PyPI explicitly after recovery.

See issue #837 for the v0.18.0 incident and regression requirements.

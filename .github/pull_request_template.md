## Summary

<!-- Briefly describe what this PR does and why. -->

Fixes # <!-- issue number, if applicable -->

## Changes

<!-- List the key changes made in this PR. -->

-

## Checklist

- [ ] Tests added or updated for every new/changed public function
- [ ] `make ci` passes locally (fmt + lint + type + test + drift-check + module-size-check + doc-snippets-check + readme-version-check + example + demo)
- [ ] `CHANGELOG.md` updated under `## [Unreleased]`
- [ ] Docstrings added for all new public APIs (Google-style)
- [ ] Public-API change? Regenerated `api/public_api.txt` with `make api` (gated by `make api-check`)
- [ ] Every modified module stays ≤ 300 lines (enforced by `make module-size-check`; or a decomposition issue is linked above)
- [ ] Related issue linked in the summary above
- [ ] Agent-facing docs updated if pipeline, API, or conventions changed

## Notes for reviewers

<!-- Anything the reviewer should pay special attention to, known limitations, follow-up work, etc. -->

## Reproducibility (scoring / context-pipeline changes)

<!-- Encouraged but not required (issue #211, Round 2 Q3=B). When the PR
     touches routing, scoring, tokenisation, or the context pipeline, paste
     the auto-generated benchmark-delta comment summary here and call out any
     matrix cells with a ⚠️ marker. Run `make benchmark-matrix && make
     scorecard` locally when possible; CI will post the sticky delta comment
     regardless. -->

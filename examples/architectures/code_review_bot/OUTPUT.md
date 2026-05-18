# Code-review bot — captured run

Output of `python examples/architectures/code_review_bot/main.py` from a
clean checkout. Deterministic: routing is seed-stable, tool responses are
canned, the firewall is hash-based on artifact content. The review walks
six steps; the routing scoreboard at the end reports the intent /
shortlist match rate, and the firewall scoreboard reports how many tool
results were compacted.

```
============================================================================
contextweaver -- Code-review bot reference architecture
============================================================================
Loaded catalog: 24 tools from catalog.yaml

============================================================================
Step 1
============================================================================
reviewer: show me the diff of this pull request against main
routed:   ['git.diff', 'review.summarize_diff', 'review.approve']
chosen:   git.diff  (intent='git.diff', in shortlist)
route prompt: 1 items / 12 tokens
firewall: 24,782 chars -> 247-char summary (artifact artifact:result:tc1)
answer prompt: included=3  dropped=0  dedup=0  closures=0  tokens=76

============================================================================
Step 2
============================================================================
reviewer: grep for the symbol legacy_charge in the codebase
routed:   ['grep.symbol', 'grep.regex', 'git.blame']
chosen:   grep.symbol  (intent='grep.symbol', in shortlist)
route prompt: 2 items / 24 tokens
firewall: 2,464 chars -> 501-char summary (artifact artifact:result:tc2)
answer prompt: included=6  dropped=0  dedup=0  closures=0  tokens=217

============================================================================
Step 3
============================================================================
reviewer: run the test suite for the changed module
routed:   ['test.run_module', 'test.run', 'git.diff_files']
chosen:   test.run_module  (intent='test.run_module', in shortlist)
route prompt: 3 items / 34 tokens
answer prompt: included=9  dropped=0  dedup=0  closures=0  tokens=285

============================================================================
Step 4
============================================================================
reviewer: run mypy on the changed module to surface type errors
routed:   ['typecheck.run', 'typecheck.module', 'typecheck.stubs']
chosen:   typecheck.module  (intent='typecheck.module', in shortlist)
route prompt: 4 items / 47 tokens
answer prompt: included=12  dropped=0  dedup=0  closures=0  tokens=360

============================================================================
Step 5
============================================================================
reviewer: run ruff on the changed files and report style violations
routed:   ['lint.run', 'test.coverage', 'lint.format_check']
chosen:   lint.run  (intent='lint.run', in shortlist)
route prompt: 5 items / 61 tokens
answer prompt: included=15  dropped=0  dedup=0  closures=0  tokens=420

============================================================================
Step 6
============================================================================
reviewer: post a review comment requesting changes on the regression
routed:   ['review.post_comment', 'review.request_changes', 'git.blame']
chosen:   review.post_comment  (intent='review.post_comment', in shortlist)
route prompt: 6 items / 75 tokens
answer prompt: included=18  dropped=0  dedup=0  closures=0  tokens=472

============================================================================
Persisted facts (carry across review steps)
============================================================================
  pr.target_file = payments/charge.py
  pr.test_status = 2 failed (legacy_charge support, decimal precision)
  pr.type_errors = 2 errors (missing charge_v2.charge, int/Decimal mismatch)

============================================================================
Firewall scoreboard
============================================================================
firewall fires: 2/6
artifacts kept: 6
(Each firewall fire compacts a >2 KB tool result down to a 500-char summary;
 raw bytes stay addressable in the artifact store for drilldown.)

============================================================================
Routing scoreboard
============================================================================
intent in router top-3: 6/6  (100%)
```

## Reading the output

- **Step 1.** The 28 KB diff dump is routed correctly to `git.diff` and
  hits the firewall: 24,782 raw chars compact to a 247-char summary.
  The artifact is parked at `artifact:result:tc1` and stays addressable
  for drilldown.
- **Step 2.** The grep hit-list (~2.5 KB) also exceeds the 2 KB
  threshold and fires the firewall a second time.
- **Steps 3–5.** Test / typecheck / lint results are small enough to land
  on the prompt verbatim — no firewall fire.
- **Step 6.** The bot posts the review comment (a `side_effects: true`
  tool) only after all four prior steps have informed it.
- **Routing scoreboard.** Every intent lands in the router's top-3
  shortlist (`6/6`) — the catalog tokenisation is healthy for this
  domain at this scale. If it weren't, the bot would fall back to
  `shortlist[0]` (best-rank pick) rather than fail.
- **Firewall scoreboard.** 2 of 6 tool results compacted; 6 artifacts
  parked (every tool result is artifact-addressable even below the
  firewall threshold, so drilldown works regardless).

# Product validation protocol

ContextWeaver's current product thesis is deliberately falsifiable:

> A controlled, provider-independent capability surface can make agent capabilities easier to reproduce, inspect, evaluate and diff before runtime.

The first research question is **not** whether people like ContextWeaver. It is whether qualified teams already experience a painful enough problem to justify another explicit layer.

This protocol implements the evidence gates in #758 and #840. It is not launch copy and should not be used to solicit stars, votes, or coordinated engagement.

## Cohorts

Keep these populations separate in analysis:

1. **Design partners** — high-touch qualitative discovery. Maintainer assistance is expected and must not be counted as unassisted onboarding evidence.
2. **Unassisted evaluators** — external users who may read public docs/issues but receive no synchronous setup help before the first-success measurement ends.
3. **Persistent integrations** — genuine external projects/workflows followed over time (about 7 and 30 days where permission exists).

Do not combine the cohorts into one success rate.

## Design-partner selection

Target approximately five independent teams/developers meaningfully exposed to heterogeneous agent capabilities, such as:

- multiple MCP servers;
- OpenAPI/internal API tools;
- framework-native functions/tools;
- Agent Skills/A2A or other source formats;
- multiple environments/providers/hosts;
- non-trivial capability release/change processes.

Prefer real operating workflows over toy demos. Do not choose only existing project enthusiasts.

## Neutral interview script

Do **not** describe ContextWeaver's proposed solution until after the current workflow, severity and alternatives are understood.

### Current workflow

1. How do you know exactly which tools/capabilities a particular deployed agent version had available?
2. When a server/API/tool definition changes, how do you detect what changed before deployment?
3. How do you test whether a changed capability surface still routes/calls correctly before promotion?
4. Can you reproduce the same effective capability set across environments/providers/hosts?
5. Have duplicate, ambiguous, stale or incompatible capability definitions caused an incident or debugging session?
6. How are capability definitions/configuration reviewed and versioned today?

### Severity

7. How often does the problem occur?
8. What is the consequence: developer time, failed tasks, incidents/regressions, cost, security/review friction, or mostly annoyance?
9. What is the current workaround and what does it cost?
10. Which part is hardest: discovery, normalization, identity, drift, evaluation, promotion, runtime selection, result/context handling, or something else?

### Existing alternatives

11. Which provider-native, gateway, framework or config-as-code mechanisms already solve part of this?
12. Where are provider-native tool search, ordinary tests/configuration or an MCP gateway fully sufficient?
13. What would make an additional compile/evaluate step unacceptable?

### Only now show the proposed workflow

Describe the smallest relevant shape, not the whole architecture:

```text
discover → analyse → compile → eval → diff → load/run
```

Then ask:

14. Which step, if any, removes a problem you already have?
15. Which step feels like ceremony?
16. Would you try this on a genuine project? What would block that trial?
17. If it worked, would you keep it in the workflow or use it only for one-off diagnostics?

## Interview evidence record

Store a sanitized note outside the public repository unless the participant explicitly permits publication. Use an anonymized identifier by default.

Recommended fields:

```yaml
participant_id: DP-001
profile: "platform engineer; multi-MCP + internal APIs"
existing_workflow: "..."
problem_frequency: "weekly | monthly | rare"
severity: "high | medium | low"
current_workaround: "..."
alternatives_already_used:
  - "provider-native tool search"
  - "config in git"
strongest_pain_before_solution: "..."
reproducibility_drift_eval_matters: true
workflow_value:
  discover: "..."
  analyse: "..."
  compile: "..."
  eval: "..."
  diff: "..."
  load_run: "..."
ceremony_or_cost: "..."
willing_to_try_real_project: true
concrete_blockers:
  - "..."
follow_up: "..."
publication_permission: false
```

Do not collect credentials, proprietary capability definitions, customer data or internal incident details that are not necessary to answer the research question.

## H1 decision rubric

### Strong supporting evidence

- at least three independent teams describe reproducibility/drift/evaluation as a meaningful **existing** problem before seeing the solution;
- current workarounds are materially manual, fragmented or provider-specific;
- at least two teams are willing to try a genuine project integration;
- at least one valued benefit is not simply generic tool search or lower prompt size.

### Mixed evidence

- pain exists only at unusual scale;
- teams value diff/diagnostics as an occasional tool but reject a persistent build step;
- provider-native/gateway mechanisms solve most runtime pain and only a narrow offline diagnostic surface remains useful.

Mixed evidence should narrow the product rather than automatically expand the architecture.

### Negative / kill evidence

- qualified teams consider the problem low severity;
- ordinary config/version control/tests solve it cheaply;
- teams reject the extra compilation/evaluation artifact;
- interest is mainly in generic tool search/token reduction already available natively;
- willingness to try exists only after heavy maintainer hand-holding or because the project itself is interesting.

Preserve negative evidence. Do not translate a failed hypothesis into a request for more features.

## Unassisted first-success protocol

The unassisted cohort measures a different question: can a stranger evaluate the product without the maintainer?

A meaningful success is not `pip install` or `--help`. It should produce at least one useful receipt such as:

- discovered capability/source inventory;
- ambiguity/duplicate/coverage finding;
- compiled bundle identity/provenance;
- evaluation pass/fail;
- candidate-vs-last-good diff;
- load/route/hydrate from an evaluated bundle.

Record:

- scenario/version;
- assisted vs unassisted cohort;
- install success;
- seconds to first meaningful receipt;
- whether real/representative sources were connected;
- first blocking failure category;
- maintainer interventions;
- rollback/removal result;
- sanitized free-text friction.

The machine-readable schema/reporting path lives under `benchmarks/onboarding/`.

## Retention

With explicit permission, follow genuine integrations around 7 and 30 days. Record whether they are:

- active independently;
- active but maintainer-operated;
- removed/abandoned (and why);
- indeterminate/no follow-up.

A retained integration is stronger evidence than a successful demo. A removed integration is also useful evidence.

## Product decision

At the end of the validation window, #758 must choose one of:

- **go** — strong external pull for reproducibility/evaluation/drift;
- **narrow** — only a smaller diagnostic/routing/context surface has evidence;
- **stop expanding / rethink** — incremental value does not justify the layer.

No outcome is automatically interpreted as `build more`.

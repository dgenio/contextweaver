# Knowledge-bundle context sources (OKF, repo knowledge, lessons, expertise packs)

Four related adapters let contextweaver ingest external knowledge stored as
Markdown files with YAML frontmatter — the OKF convention — and expose it as
bounded, selectable context candidates that flow through the existing
candidate selection, budget, dedup, sensitivity, and rendering pipeline. None
of them require network access or a runtime dependency beyond PyYAML, which
is already a core dependency.

| Adapter | Module | Use it for |
|---|---|---|
| OKF bundle loader | `contextweaver.adapters.okf` | Generic OKF-format knowledge bundles |
| Repository knowledge | `contextweaver.adapters.repo_knowledge` | Generated repo wikis, `docs/agent-context/`, `AGENTS.md`-style docs |
| Lessons | `contextweaver.adapters.lessons` | LessonWeaver-exported lessons, with lifecycle filtering |
| Expertise packs | `contextweaver.adapters.expertise_pack` | Structured constraints/assumptions with conflict detection |

All four share one permissive parsing core (`contextweaver.adapters._okf_io`):
a missing frontmatter fence, invalid YAML, or a non-mapping frontmatter value
degrades to a diagnostic plus a best-effort node — it never raises, unless
you opt in with `on_invalid="raise"`.

## OKF bundle loader

An OKF bundle is a directory of `.md` files with YAML frontmatter, plus two
optional bundle-level files: `index.md` (overview metadata) and `log.md`
(bundle history) — neither is loaded as ordinary concept content.

```python
from contextweaver.adapters.okf import load_okf_bundle, select_knowledge

bundle = load_okf_bundle("path/to/okf-bundle")
items = select_knowledge(bundle.nodes, "context firewall", budget_tokens=2000)
```

**When to prefer OKF over normal event-log ingestion:** use OKF when the
knowledge is *external, versioned, and reusable across sessions* — a shared
concept library, not this session's conversation. Use the normal event log
(`ContextManager.ingest`) for anything session-specific: tool calls, tool
results, user turns. OKF nodes and event-log items can both be selected into
the same context build; they are independent candidate sources.

Unknown frontmatter fields are preserved verbatim under each node's
`frontmatter` attribute, and every field surfaces in the materialised
`ContextItem.metadata["frontmatter"]` — nothing is silently dropped.

## Repository knowledge

Narrows the OKF loader to repo documentation: generated wikis, architecture
notes, module summaries, `AGENTS.md`/`CLAUDE.md`-style instruction files.
Unlike the OKF loader, plain Markdown files with no frontmatter at all are
still valid candidates (their title falls back to the filename), and
`index.md`/`log.md` carry no special meaning — this is a documentation tree,
not an OKF bundle proper.

```python
from contextweaver.adapters.repo_knowledge import load_repo_knowledge, select_repo_knowledge

bundle = load_repo_knowledge("docs/agent-context", max_files=200)
debugging_docs = select_repo_knowledge(
    bundle.nodes, "why is routing dropping candidates", budget_tokens=2000, usage_tag="debugging"
)
```

References inside a document (`AGENTS.md` links, a node's own `links` field)
are never auto-followed — the loader only reads files under the directory
you point it at, so a documentation tree cannot force-load content beyond
its own root.

Relation to the plain OKF loader: `repo_knowledge` is a thin, purpose-specific
layer over the same core — it adds the plain-Markdown fallback, size
guardrails (`max_files`/`max_total_bytes`), and deterministic usage-tag
classification (`classify_usage`, e.g. `"debugging"`, `"onboarding"`). These
tags are plain metadata strings, not contextweaver `Phase` values.

## Lessons (LessonWeaver-exported bundles)

Lessons differ from repository-knowledge nodes in one key way: **lifecycle
status governs eligibility, not just relevance.** A lesson's `status`
(`candidate`/`reviewed`/`active`/`deprecated`/`rejected`), `scope`, and
`expires_at` decide whether it is even a candidate for selection — repository
documentation nodes have no such gate.

```python
from contextweaver.adapters.lessons import (
    LessonSelectionPolicy,
    load_lesson_bundle,
    select_lessons,
)

nodes, _diagnostics = load_lesson_bundle("path/to/lessonweaver-export")
items, excluded = select_lessons(
    nodes,
    "api design",
    budget_tokens=1500,
    policy=LessonSelectionPolicy(preferred_scope="project"),
)
```

By default, `rejected` and `deprecated` lessons are excluded, and unreviewed
`candidate` lessons are excluded unless you opt in with
`LessonSelectionPolicy(include_candidates=True)`. Every exclusion is reported
back with a reason (`"status:rejected"`, `"expired"`, ...) so you can surface
lifecycle diagnostics rather than silently dropping content.

**End-to-end sketch:** LessonWeaver reviews traces and exports reviewed
lessons as OKF-style Markdown nodes → contextweaver's `select_lessons` picks
the subset relevant to the current task, honoring lifecycle status → a
downstream ChainWeaver flow step can reference the selected lesson IDs (via
each item's `metadata["_contextweaver"]["knowledge_source"]["id"]`) as
provenance for why a particular constraint was applied.

## Expertise packs

An ExpertisePack is a directory bundle of constraint/assumption/verification/
failure-mode nodes. Each node's frontmatter `key` groups related constraints
(e.g. `"api-style"`, `"verification-command"`); an `index.md` declares the
pack's `version`.

```python
from contextweaver.adapters.expertise_pack import (
    detect_conflicts,
    expertise_pack_to_context_items,
    load_expertise_pack,
)

pack = load_expertise_pack("path/to/expertise-pack")
findings = detect_conflicts(pack.nodes, task_tags={"python-library"})
items = expertise_pack_to_context_items(pack, task_tags={"python-library"})
```

Conflict detection is deterministic and literal: it flags constraints that
share a `key` but disagree on text, restricted to nodes that are live
(not expired) and applicable to the given `task_tags`. It does not perform
natural-language contradiction inference — that would require a model call,
which core knowledge-source loading deliberately does not make. Pack
sections only enter bounded context "when relevant" — expired or
inapplicable nodes are excluded by `expertise_pack_to_context_items`, never
injected unconditionally.

**Consuming packs generated by LessonWeaver:** LessonWeaver can export
distilled expertise (goals, constraints, known failure modes) in the same
OKF-style Markdown-plus-frontmatter shape `load_expertise_pack` expects —
point the loader at LessonWeaver's export directory the same way you would
any other ExpertisePack. The pack's `key` field is what LessonWeaver should
use to group related constraints so `detect_conflicts` can catch
contradictions across export runs.

The canonical ExpertisePack schema is tracked externally at
`dgenio/weaver-spec#184`. This adapter validates pack **structure** (an
`index.md` declaring a version, every node carrying a `key`) rather than
that full external schema; see the module docstring in
`contextweaver.adapters.expertise_pack` for the seam to bind it later.

## See also

- [`examples/knowledge_bundles_demo.py`](https://github.com/dgenio/contextweaver/blob/main/examples/knowledge_bundles_demo.py) — a runnable, self-contained walkthrough of all four adapters.
- [Core Concepts](../concepts.md) for `ContextItem`, `Sensitivity`, and the candidate-selection pipeline these adapters feed into.

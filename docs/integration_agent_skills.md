# Agent Skills (SKILL.md) integration

`contextweaver.adapters.agent_skills` loads [Agent Skills](https://github.com/anthropics/skills)
directories — the open `SKILL.md` format — into the routing catalog as
`SelectableItem`s. Only the frontmatter is read to route; the full Markdown
body is hydrated lazily once a skill is selected. "Which of my 200 skills
applies here?" is the same context-rot problem as "which of my 200 tools," and
contextweaver answers it deterministically.

> **Skills as routable capabilities vs skills as guidance.** This adapter treats
> a skill as a *routable capability* — a candidate the router can shortlist.
> That is distinct from [`docs/interop_skill_cards.md`](interop_skill_cards.md),
> which maps *guidance* skill cards onto `ContextItem`s for the prompt. Use this
> adapter when you want the router to pick a skill; use skill cards when you want
> a skill's guidance text injected as context.

## Scope

- **In scope:** loading local skill directories, routing over them, lazy body /
  resource hydration on selection.
- **Out of scope:** skill *execution* (contextweaver never executes) and
  marketplace fetch/install (local directories only).

## Layout

A skill is a directory containing a `SKILL.md` with `---`-delimited YAML
frontmatter (`name` and `description` required) plus optional support files:

```text
skills/
  pdf/
    SKILL.md
    reference.md
  xlsx/
    SKILL.md
```

```markdown
---
name: pdf
description: Extract text and tables from PDF documents.
tags: [documents, pdf]
---
# PDF skill
Detailed, possibly large, step-by-step body...
```

## Routing over a skills library

```python
from contextweaver.adapters.agent_skills import SkillBodySource, load_skills_catalog
from contextweaver.routing.router import Router
from contextweaver.routing.tree import TreeBuilder

catalog = load_skills_catalog("skills/")        # frontmatter only
items = catalog.all()
router = Router(TreeBuilder().build(items), items=items, beam_width=3)

result = router.route("pull the tables out of a PDF report")
chosen = result.candidate_ids[0]

# Hydrate the full body only after the skill is chosen.
body_source = SkillBodySource.from_catalog(catalog)
body = body_source.get_body(chosen)
resources = body_source.get_resources(chosen)   # bundled files, loading left to you
```

## Mapping

| SKILL.md | `SelectableItem` |
|---|---|
| `name` (required) | `id` (`skills:{name}`) and `name` |
| `description` (required) | `description` (routable summary) |
| `tags` (optional) | `tags` (merged with the `skill` tag) |
| other frontmatter | `metadata["frontmatter"]` |
| directory | `metadata["skill_path"]`, `metadata["runtime"] = "agent-skills"` |

## Safety

Skill bodies are arbitrary, untrusted Markdown. Hydration (not inlining) plus
the context firewall is the mitigation: route the resolved body through the
firewall when ingesting it as context, and treat the text with the same caution
as upstream tool descriptions. The parser tolerates unknown frontmatter keys so
spec growth does not break loading.

A runnable, network-free example lives at
[`examples/skills_routing_demo.py`](https://github.com/dgenio/contextweaver/blob/main/examples/skills_routing_demo.py).

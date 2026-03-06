# contextweaver — Agent Guide

## Repository purpose
contextweaver is a Python library for dynamic context management for tool-using AI agents.
It provides two integrated engines:
1. **Context Engine** — phase-specific budgeted context compilation with a context firewall
2. **Routing Engine** — bounded-choice navigation over large tool catalogs via DAG + beam search

## Architecture overview

| Module / Package | Responsibility |
|---|---|
| `src/contextweaver/types.py` | All core dataclasses and enums (SelectableItem, ContextItem, Phase, …) |
| `src/contextweaver/config.py` | ScoringConfig, ContextBudget, ContextPolicy |
| `src/contextweaver/protocols.py` | Protocol definitions (TokenEstimator, EventHook, Summarizer, …) |
| `src/contextweaver/exceptions.py` | All custom exception classes |
| `src/contextweaver/_utils.py` | Text similarity: tokenize(), jaccard(), TfIdfScorer |
| `src/contextweaver/serde.py` | Serialisation helpers for to_dict/from_dict patterns |
| `src/contextweaver/store/` | InMemoryArtifactStore, InMemoryEventLog, InMemoryEpisodicStore, InMemoryFactStore |
| `src/contextweaver/summarize/` | SummarizationRule, RuleEngine, extract_facts() |
| `src/contextweaver/context/` | Full context pipeline: candidates → sensitivity filter → firewall → scoring → dedup → selection → prompt |
| `src/contextweaver/routing/` | Catalog, ChoiceGraph, TreeBuilder, Router (beam search), cards renderer |
| `src/contextweaver/adapters/` | MCP and A2A protocol adapters |
| `src/contextweaver/__main__.py` | CLI: 7 subcommands (demo, build, route, print-tree, init, ingest, replay) |

## Commands

```bash
make fmt      # ruff format src/ tests/ examples/
make lint     # ruff check src/ tests/ examples/
make type     # mypy src/
make test     # pytest -q
make example  # run all example scripts
make demo     # python -m contextweaver demo
make ci       # fmt + lint + type + test
```

After cloning, run `pre-commit install` once to activate the git hooks. The hooks
run `ruff format` and `ruff check --fix`, plus standard file hygiene checks,
automatically on every `git commit`.

## Conventions
- Python ≥ 3.10, zero runtime dependencies
- All public APIs: type hints + docstrings
- Context engine is async-first (build()), with build_sync() wrapper
- Routing engine is synchronous (pure computation)
- All dataclasses implement to_dict() / from_dict()
- All errors use custom exceptions from exceptions.py
- Text similarity: always use _utils.py (never duplicate)
- Target ≤ 300 lines per module (except __main__.py)
- Deterministic by default: tie-break by ID, sorted keys

## How to add a feature safely
1. Identify the relevant module (see table above).
2. Add or modify only the targeted module.
3. Update protocols.py if adding a new protocol.
4. Add tests in tests/test_<module>.py.
5. Run `make ci` to verify.
6. Update CHANGELOG.md.

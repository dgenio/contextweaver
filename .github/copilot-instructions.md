# contextweaver

This is contextweaver, a Python library for dynamic context management for AI agents.

## Architecture
- src/contextweaver/ contains the library
- Two engines: Context Engine (phase-specific context building) and Routing Engine (bounded-choice tool routing)
- All stores (EventLog, ArtifactStore, EpisodicStore, FactStore) are protocol-based with InMemory defaults
- Context pipeline: generate_candidates → sensitivity_filter → apply_firewall → score_candidates → deduplicate_candidates → select_and_pack → render_context
- Routing pipeline: TreeBuilder → ChoiceGraph → Router (beam search) → ChoiceCards

## Conventions
- Python >=3.10, zero runtime dependencies
- All public interfaces: type hints + docstrings
- Context engine is async-first with _sync wrappers
- Routing engine is sync (pure computation)
- All dataclasses implement to_dict() and from_dict()
- All errors use custom exceptions from exceptions.py
- Text similarity lives in _utils.py (single source of truth): tokenize(), jaccard(), TfIdfScorer
- Target ≤300 lines per module (except __main__.py CLI)
- Deterministic by default: tie-break by ID, sorted keys, seeded generation

## Commands
- make fmt / make lint / make type / make test / make example / make demo / make ci

## Key types
- SelectableItem (unified tool/agent/skill/internal), alias ToolCard
- ContextItem (event log entry with parent_id for dependency closure)
- ResultEnvelope (summary + facts + artifacts + views)
- ContextPack (rendered prompt + stats + BuildStats)
- ChoiceCard (LLM-friendly compact card, never includes full schemas)
- ChoiceGraph (bounded DAG, serializable, validated on load)
- MaskRedactionHook (built-in redaction hook for sensitivity enforcement)

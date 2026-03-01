# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.1.0] - Unreleased

### Added
- Initial release scaffolding
- Context Engine: phase-specific budgeted context compilation with context firewall
- Routing Engine: bounded-choice navigation via ChoiceGraph + beam search
- In-memory stores: ArtifactStore, EventLog, EpisodicStore, FactStore
- StoreBundle grouping all four stores
- Summarize sub-package: SummarizationRule, RuleEngine, extract_facts()
- Protocol definitions: TokenEstimator, EventHook, Summarizer, Extractor, RedactionHook, Labeler
- Configuration: ScoringConfig, ContextBudget, ContextPolicy
- Utility functions: tokenize(), jaccard(), TfIdfScorer
- CLI with 7 subcommands: demo, build, route, print-tree, init, ingest, replay
- MCP and A2A adapter stubs
- Example scripts: minimal_loop, tool_wrapping, routing_demo, before_after, mcp_adapter_demo, a2a_adapter_demo
- Full type annotations (PEP 561 py.typed marker)
- CI workflow (Python 3.10 / 3.11 / 3.12)

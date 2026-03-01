# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [0.0.2] - 2026-03-01

### Added
- RuleBasedSummarizer: concrete summarizer implementation
- StructuredExtractor: structured fact extraction (JSON, tabular, plain text)
- TiktokenEstimator: token counting via tiktoken (optional) with model name support
- EventLog: query(), children(), parent(), count(), tail() methods
- ArtifactStore: exists(), metadata(), drilldown() selectors (head, lines, json_keys, rows)
- EpisodicStore: latest(), delete() methods
- FactStore: list_keys() method
- Protocol declarations for all new EventLog and ArtifactStore methods
- Comprehensive test coverage (255 tests)

### Fixed
- Version alignment: pyproject.toml now matches __init__.py
- License alignment: pyproject.toml now uses Apache-2.0 (matches LICENSE file)
- EventLog.query() defensive copy (was leaking internal list reference)
- _EMAIL_RE regex: removed stray pipe from [A-Z|a-z] character class
- mypy override for optional tiktoken dependency

## [0.0.1] - 2026-03-01

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

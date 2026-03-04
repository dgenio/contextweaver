# contextweaver — Claude Guide

## Do
- Run `pre-commit install` once after cloning to activate git hooks (ruff format + check, and file hygiene checks, on every commit)
- Run `make ci` before submitting any change
- Use `make fmt` to auto-format code
- Keep each module ≤ 300 lines
- Add type hints to all public functions and methods
- Add docstrings to all public classes and functions
- Write tests for every new feature in `tests/test_<module>.py`
- Use `from __future__ import annotations` in every source file
- Import exceptions from `contextweaver.exceptions`
- Import text-similarity utilities from `contextweaver._utils`

## Don't
- Add runtime dependencies (zero-dependency is a hard constraint)
- Put logic in `__init__.py` files (only re-exports)
- Duplicate tokenise / similarity logic outside `_utils.py`
- Use `print()` in library code (use hooks / logging)
- Mutate the event log outside of `InMemoryEventLog.append()`
- Add `from contextweaver import *` imports anywhere

## Debugging checklist
1. `make lint` — check for style / import errors
2. `make type` — check for type errors
3. `make test` — run the test suite
4. Check `BuildStats` fields to understand what the context engine dropped and why
5. Use `ContextManager.artifact_store.list_refs()` to inspect intercepted tool outputs

## Module responsibility map
```
types.py        → dataclasses & enums (no logic)
config.py       → configuration dataclasses (no logic)
protocols.py    → Protocol interfaces (no logic)
exceptions.py   → exception hierarchy (no logic)
_utils.py       → text similarity (tokenize, jaccard, TfIdfScorer)
serde.py        → to_dict/from_dict helpers
store/          → in-memory data stores (append-only event log, artifact store, …)
summarize/      → rule engine + fact extraction
context/        → full context compilation pipeline (incl. sensitivity.py for sensitivity enforcement)
routing/        → catalog, DAG, beam-search router, card renderer
adapters/       → MCP and A2A protocol conversion
__main__.py     → CLI entry point
```

## How to run tests
```bash
pip install -e ".[dev]"
pytest -q                    # all tests
pytest tests/test_utils.py   # single file
pytest -k "test_jaccard"     # single test
```

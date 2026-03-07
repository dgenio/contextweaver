# Review Checklist

Use this checklist for both agent self-check (before proposing changes) and
maintainer review (when reviewing PRs). Items are grouped by category.

## Validation

- [ ] `make ci` passes (all 6 targets: fmt, lint, type, test, example, demo)
- [ ] No new warnings introduced

## Hard Rules

- [ ] No `print()` in library code (exempt: `__main__.py`)
- [ ] No business logic in `__init__.py` (only re-exports)

## Code Quality

- [ ] Type hints on all new public functions and methods
- [ ] Google-style docstrings on all new public classes and functions
- [ ] `from __future__ import annotations` in any new or modified file
- [ ] Exceptions use custom types from `contextweaver.exceptions`
- [ ] New modules ≤ 300 lines (exempt: `types.py`, `envelope.py`, `__main__.py`)
- [ ] 100-character line length respected

## Testing

- [ ] Tests added for new functionality in `tests/test_<module>.py`
- [ ] Async tests use `pytest.mark.asyncio`
- [ ] No mocking of internal modules — uses real in-memory implementations

## Architectural Consistency

- [ ] No runtime dependencies added to core (`install_requires` stays empty)
- [ ] `context/` code is async-first with `_sync` wrappers
- [ ] `routing/` code is sync-only
- [ ] Store changes implement the relevant protocol from `protocols.py`
- [ ] Adapter changes are pure stateless converters
- [ ] No text similarity logic duplicated outside `_utils.py`
- [ ] `to_dict()` / `from_dict()` added to any new dataclass
- [ ] Event log mutations only via `append()`

## Pipeline Integrity

- [ ] Context pipeline stage order preserved (8 stages — see [invariants](invariants.md))
- [ ] Dependency closure not bypassed or weakened
- [ ] Sensitivity defaults not weakened
- [ ] Changes to `context/sensitivity.py` received extra security scrutiny

## Documentation

- [ ] `CHANGELOG.md` updated under `## [Unreleased]`
- [ ] Module map in `AGENTS.md` updated if modules were added/removed/renamed
- [ ] Agent-facing docs updated if pipeline, API, or conventions changed
- [ ] Examples/demos updated if feature is user-facing
- [ ] No contradictions introduced between `AGENTS.md` and supporting docs

## Cross-File Consistency

- [ ] Pipeline stage count/order matches across all docs and code
- [ ] Command descriptions match `Makefile`
- [ ] Module map matches filesystem
- [ ] Convention changes reflected in both `AGENTS.md` and `CONTRIBUTING.md`

## Invariant Spot-Checks

If the change touches any of these areas, verify the corresponding invariant:

| Area touched | Verify |
|---|---|
| Pipeline stages | 8-stage order preserved, dependency closure intact |
| `sensitivity.py` | Defaults not weakened, security review done |
| Store protocols | Protocol interface unchanged or backward-compatible |
| `serde.py` or `to_dict`/`from_dict` | Both mechanisms still in use, not consolidated |
| `_utils.py` | No similarity logic duplicated elsewhere |
| `__init__.py` files | Only re-exports, no logic |
| `envelope.py` or `types.py` | No I/O added to data layer |

## Update Triggers

Update this checklist when:
- New hard rules or invariants are established.
- New review gates are identified from recurring review feedback.
- Definition of done changes (sync with [workflows.md](workflows.md)).

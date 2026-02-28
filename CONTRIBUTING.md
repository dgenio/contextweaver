# Contributing to contextweaver

Thank you for your interest in contributing!

## Setup

```bash
git clone https://github.com/dgenio/contextweaver
cd contextweaver
pip install -e ".[dev]"
```

## Development workflow

```bash
make fmt    # auto-format with ruff
make lint   # lint with ruff
make type   # type-check with mypy
make test   # run tests with pytest
make ci     # run all of the above in sequence
```

All CI checks must pass before a PR can be merged.

## PR process

1. Fork the repository and create a feature branch from `main`.
2. Make your changes following the style guide below.
3. Add or update tests in `tests/`.
4. Run `make ci` and ensure it passes.
5. Update `CHANGELOG.md` under `## [Unreleased]`.
6. Open a pull request with a clear description of the change.

## Style guide

- **Python ≥ 3.10** — use `X | Y` union syntax, `match` statements where appropriate.
- **Zero runtime dependencies** — do not add any `install_requires` entries.
- **Type hints everywhere** — all public functions and methods must be fully annotated.
- **Docstrings** — use Google-style docstrings on all public classes and functions.
- **Line length** — 100 characters maximum (enforced by ruff).
- **Imports** — `from __future__ import annotations` at the top of every file.
- **Module size** — target ≤ 300 lines per module (except `__main__.py`).
- **Determinism** — all algorithms must be deterministic; tie-break by ID / sorted keys.

## Testing requirements

- Every new public function must have at least one test.
- Tests live in `tests/test_<module_name>.py`.
- Use `pytest.mark.asyncio` for async tests (asyncio_mode = "auto" is set globally).
- Do not mock internal modules; use real in-memory implementations.

## Adding a new store backend

1. Implement the store class in `src/contextweaver/store/<name>.py`.
2. Export it from `src/contextweaver/store/__init__.py`.
3. Add tests in `tests/test_store_<name>.py`.
4. Update `StoreBundle` in `store/__init__.py` if appropriate.

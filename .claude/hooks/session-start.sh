#!/bin/bash
set -euo pipefail

# Only run in Claude Code on the web (remote container).
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
    exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(pwd)}"

# Install the package in editable mode with the dev extra. This pulls in the
# core runtime deps plus pytest, pytest-asyncio, pytest-cov, ruff, mypy,
# pre-commit, weaver_contracts, and jsonschema — everything the Makefile's
# fmt / lint / type / test targets require.
python3 -m pip install --quiet --user -e ".[dev]"

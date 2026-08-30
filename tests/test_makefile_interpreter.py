"""The Makefile must reach tools through the project interpreter, not PATH.

Issue #712 established `$(PYTHON)` / `$(PIP)` as this repository's convention,
but `fmt`, `lint`, `type`, `docs` and `docs-serve` never adopted it — they
invoked `ruff`, `mypy` and `mkdocs` bare. Those three are installed by
`pip install -e ".[dev]"` / `".[docs]"`, so a shell whose PATH resolves them
from some other environment silently runs a different tool than the one the
project declares — and `fmt lint type` are the first three targets of
`make ci`.

Measured in a container where an unrelated `uv`-tool install came first on
PATH: `make type` reported *18 errors in 11 files*, while
`python3 -m mypy src/ examples/ scripts/` reported *Success: no issues found
in 314 source files*. A gate that reports a fact about its environment while
looking like a fact about the code is worse than no gate — it sends a reader
off "fixing" imports that were never broken.

These tests pin the convention so the next target added cannot quietly drop
back to PATH.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

MAKEFILE = Path(__file__).resolve().parent.parent / "Makefile"

# Python tools this project installs through its own extras. Reaching any of
# them via PATH means running whatever version another environment put there
# first. `uv`, `uvx` and `pipx` are deliberately absent: `floor-deps` and
# `tool-smoke` exist to build isolated environments and install the built
# wheel the way a user would, so routing them through the project interpreter
# would defeat the target.
PROJECT_TOOLS = ("ruff", "mypy", "pytest", "mkdocs", "pip")

# A tool name in *command position*: at the start of a recipe line, or after a
# shell separator. `$(PYTHON) -m ruff` is excluded by the negative lookbehind
# on `-m `, so only the bare form matches. Matching command position rather
# than the first word means a tool hidden mid-way through a compound
# `a && b` or `a; b` line is still caught.
_BARE_TOOL = re.compile(
    r"(?:^|[;&|(]\s*|&&\s*|\|\|\s*)(?<!-m )(?P<tool>" + "|".join(PROJECT_TOOLS) + r")\b",
    re.MULTILINE,
)


def recipe_text() -> str:
    """Every recipe line in the Makefile, tabs and @/- prefixes stripped."""
    lines = [
        line.lstrip("\t").lstrip("@-")
        for line in MAKEFILE.read_text(encoding="utf-8").splitlines()
        if line.startswith("\t")
    ]
    return "\n".join(lines)


def recipes() -> dict[str, list[str]]:
    """Map each target to its recipe lines."""
    found: dict[str, list[str]] = {}
    target: str | None = None
    for line in MAKEFILE.read_text(encoding="utf-8").splitlines():
        if line.startswith("\t"):
            if target is not None:
                found[target].append(line.lstrip("\t").lstrip("@-"))
            continue
        match = re.match(r"^([A-Za-z0-9_.-]+):", line)
        target = match.group(1) if match else None
        if target is not None and target != ".PHONY":
            found.setdefault(target, [])
    return found


class TestToolsGoThroughTheInterpreter:
    def test_no_recipe_invokes_a_project_tool_from_path(self) -> None:
        offenders = [match.group(0).strip() for match in _BARE_TOOL.finditer(recipe_text())]
        assert not offenders, (
            "these recipe lines run a project tool from PATH instead of "
            f"$(PYTHON) -m (#712): {offenders}"
        )

    @pytest.mark.parametrize("target", ["fmt", "lint", "type", "test"])
    def test_the_ci_gate_targets_use_the_interpreter(self, target: str) -> None:
        lines = recipes()[target]
        assert lines, f"{target} has no recipe"
        assert all("$(PYTHON) -m " in line for line in lines), lines

    @pytest.mark.parametrize(
        "line",
        [
            "mypy src/ examples/ scripts/",
            "ruff format src/",
            "cd docs && mkdocs build",
            "rm -rf build; pytest -q",
        ],
    )
    def test_the_detector_catches_a_bare_invocation(self, line: str) -> None:
        """The check that the check works — a matcher that never matches is not
        a guard, and the compound-line forms are the ones easiest to get wrong."""
        assert _BARE_TOOL.search(line), line

    @pytest.mark.parametrize(
        "line",
        [
            "$(PYTHON) -m mypy src/ examples/ scripts/",
            "$(PYTHON) -m ruff check src/",
            "$(PIP) install -e .",
        ],
    )
    def test_the_detector_accepts_the_correct_form(self, line: str) -> None:
        assert not _BARE_TOOL.search(line), line


class TestInterpreterVariable:
    def test_python_is_defined_once(self) -> None:
        text = MAKEFILE.read_text(encoding="utf-8")
        assert text.count("\nPYTHON ?= python3") == 1, (
            "PYTHON was defined twice; a duplicate default is dead weight that "
            "invites the two copies to drift"
        )

    def test_pip_is_derived_from_python(self) -> None:
        assert "PIP ?= $(PYTHON) -m pip" in MAKEFILE.read_text(encoding="utf-8")

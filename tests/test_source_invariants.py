"""Grep-based guard tests for cross-cutting source invariants (issues #463, #467).

These enforce two documented cross-cutting rules going forward, so a future
change that reintroduces the smell fails CI rather than slipping through review:

* **Custom exceptions only** — library code raises ``contextweaver.exceptions``
  types, never a bare ``ValueError`` / ``RuntimeError`` (issue #463).
* **No load-bearing asserts** — ``assert`` statements are stripped under
  ``python -O``; correctness checks must be explicit ``raise``\\s.  Type-narrowing
  asserts are allowed when annotated with a ``narrow`` comment (issue #467).
"""

from __future__ import annotations

import re
from pathlib import Path

import contextweaver

#: Root of the installed package source tree.
_SRC = Path(contextweaver.__file__).resolve().parent

#: CLI entry points are print-heavy and explicitly exempt from the
#: custom-exception rule (see AGENTS.md "Hard Rules").
_EXEMPT_FILES = {"__main__.py", "_demos.py"}

_BARE_RAISE = re.compile(r"\braise\s+(?:ValueError|RuntimeError)\s*\(")
_ASSERT_STMT = re.compile(r"^\s*assert\b")


def _library_files() -> list[Path]:
    return [p for p in _SRC.rglob("*.py") if p.name not in _EXEMPT_FILES]


def test_no_bare_value_or_runtime_error_in_library_code() -> None:
    """Library code must raise contextweaver.exceptions types, not bare builtins."""
    offenders: list[str] = []
    for path in _library_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _BARE_RAISE.search(line):
                offenders.append(f"{path.relative_to(_SRC)}:{lineno}")
    assert not offenders, (
        "raise contextweaver.exceptions types (e.g. ValidationError/ConfigError), "
        f"not bare ValueError/RuntimeError (issue #463): {offenders}"
    )


def test_no_load_bearing_asserts_in_library_code() -> None:
    """No load-bearing asserts (stripped under -O); type-narrowing ones must say so."""
    offenders: list[str] = []
    for path in _library_files():
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if _ASSERT_STMT.match(line) and "narrow" not in line.lower():
                offenders.append(f"{path.relative_to(_SRC)}:{lineno}")
    assert not offenders, (
        "convert load-bearing asserts to explicit raises, or annotate a "
        "type-narrowing assert with a 'narrow' comment (issue #467): "
        f"{offenders}"
    )

#!/usr/bin/env python3
"""Execute the Python snippets in README and key docs in CI (issue #526).

The README quickstart and the docs adoption pages are the project's
highest-traffic code — the first thing a prospective adopter copies.  The repo
gates ``examples/`` (``make example``), schemas, scorecards, and ``llms.txt``,
but the inline doc snippets had no execution gate, so an API rename could
silently break the exact lines the docs advertise.  This check extracts every
```python``` fence from an allowlist of docs and executes it, so a broken first
copy-paste fails CI instead of an adopter.

Blocks in an allowlisted file run **in document order, sharing one namespace**
(tutorial style: later blocks may use names defined earlier).  An intentionally
illustrative block — one that references files or variables it never defines —
opts out with an HTML comment on the line immediately above its fence::

    <!-- snippet: skip (illustrative; needs a real catalog.json) -->
    ```python
    catalog = load_catalog_json("catalog.json")
    ```

Usage::

    python scripts/check_doc_snippets.py            # execute all allowlisted snippets
    python scripts/check_doc_snippets.py --list       # list blocks + skip status

Wired into ``make doc-snippets-check`` and gating CI.
"""

from __future__ import annotations

import argparse
import contextlib
import io
import os
import tempfile
import traceback
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

# Curated allowlist of adopter-facing docs whose snippets must run. Keep this
# tight: every file here is a promise that its Python blocks execute against the
# current API. Illustrative blocks opt out per-block with the skip marker.
DOC_FILES = (
    "README.md",
    "docs/quickstart.md",
)

SKIP_MARKER = "<!-- snippet: skip"
_FENCE = "```"


@dataclass(frozen=True)
class Snippet:
    """One fenced ``python`` block extracted from a doc file."""

    file: str
    index: int
    line: int
    code: str
    skip: bool


def extract_snippets(path: Path, rel: str) -> list[Snippet]:
    """Extract ``python`` fences from *path*, flagging skip-marked blocks."""
    lines = path.read_text(encoding="utf-8").splitlines()
    snippets: list[Snippet] = []
    i = 0
    count = 0
    while i < len(lines):
        if lines[i].strip() == f"{_FENCE}python":
            # The skip marker must sit on the line *immediately* above the fence
            # (no intervening blank lines) so the opt-out is unambiguous.
            skip = i > 0 and SKIP_MARKER in lines[i - 1]
            start = i + 1
            body: list[str] = []
            i += 1
            while i < len(lines) and lines[i].strip() != _FENCE:
                body.append(lines[i])
                i += 1
            snippets.append(
                Snippet(file=rel, index=count, line=start, code="\n".join(body), skip=skip)
            )
            count += 1
        i += 1
    return snippets


def _run_file(snippets: list[Snippet]) -> list[str]:
    """Execute a file's non-skipped snippets in one namespace; return failures."""
    failures: list[str] = []
    namespace: dict[str, object] = {"__name__": "__doc_snippet__"}
    for snippet in snippets:
        if snippet.skip:
            continue
        # Sandbox the working directory so file-writing snippets never touch the
        # repo, and swallow snippet stdout (only surfaced on failure).
        with tempfile.TemporaryDirectory() as tmp:
            cwd = os.getcwd()
            os.chdir(tmp)
            buffer = io.StringIO()
            try:
                with contextlib.redirect_stdout(buffer):
                    exec(
                        compile(snippet.code, f"{snippet.file}#block{snippet.index}", "exec"),
                        namespace,
                    )
            except Exception:  # noqa: BLE001 — report any snippet failure uniformly
                captured = buffer.getvalue()
                report = f"{snippet.file} block #{snippet.index} (line {snippet.line}):\n"
                report += traceback.format_exc()
                if captured:
                    report += f"\n--- captured stdout before failure ---\n{captured}"
                failures.append(report)
            finally:
                os.chdir(cwd)
    return failures


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument(
        "--list",
        action="store_true",
        help="List discovered blocks and whether each is executed or skipped.",
    )
    args = parser.parse_args(argv)

    all_snippets: list[Snippet] = []
    for rel in DOC_FILES:
        path = REPO_ROOT / rel
        if not path.exists():
            print(f"doc-snippets: allowlisted file missing: {rel}")
            return 1
        all_snippets.extend(extract_snippets(path, rel))

    if args.list:
        for snippet in all_snippets:
            state = "skip" if snippet.skip else "run "
            print(f"  [{state}] {snippet.file} block #{snippet.index} (line {snippet.line})")
        return 0

    failures: list[str] = []
    for rel in DOC_FILES:
        failures.extend(_run_file([s for s in all_snippets if s.file == rel]))

    runnable = sum(1 for s in all_snippets if not s.skip)
    if failures:
        print(f"doc-snippets: {len(failures)} snippet(s) failed to execute:\n")
        for failure in failures:
            print(failure)
        return 1
    print(f"doc-snippets: OK ({runnable} executed, {len(all_snippets) - runnable} skipped)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""Tests for the doc-snippet execution gate (issue #526).

The gate executes Python fences from README + a curated docs allowlist so the
first code an adopter copies is guaranteed to run. These tests pin the fence
parser, the skip-marker contract, and the pass/fail behaviour against synthetic
docs (independent of the live README content).
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import check_doc_snippets  # noqa: E402  (import after sys.path manipulation)

_DOC = """\
# Title

```python
x = 1 + 1
assert x == 2
```

<!-- snippet: skip (illustrative) -->
```python
this is not valid python at all
```

Some prose with a shell block that must be ignored:

```bash
echo hi
```
"""


def test_extract_flags_skip_and_ignores_non_python(tmp_path: Path) -> None:
    doc = tmp_path / "d.md"
    doc.write_text(_DOC, encoding="utf-8")
    snippets = check_doc_snippets.extract_snippets(doc, "d.md")
    assert len(snippets) == 2  # the bash block is not extracted
    assert snippets[0].skip is False
    assert snippets[1].skip is True


def test_committed_docs_execute() -> None:
    """The real README + quickstart allowlist must execute cleanly."""
    assert check_doc_snippets.main([]) == 0


def test_failing_snippet_is_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    doc = tmp_path / "broken.md"
    doc.write_text("```python\nraise ValueError('boom')\n```\n", encoding="utf-8")
    monkeypatch.setattr(check_doc_snippets, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(check_doc_snippets, "DOC_FILES", ("broken.md",))
    rc = check_doc_snippets.main([])
    assert rc == 1
    assert "boom" in capsys.readouterr().out


def test_skip_marked_invalid_block_does_not_fail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    doc = tmp_path / "d.md"
    doc.write_text(_DOC, encoding="utf-8")
    monkeypatch.setattr(check_doc_snippets, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(check_doc_snippets, "DOC_FILES", ("d.md",))
    assert check_doc_snippets.main([]) == 0


def test_missing_allowlisted_file_fails(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(check_doc_snippets, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(check_doc_snippets, "DOC_FILES", ("nope.md",))
    assert check_doc_snippets.main([]) == 1

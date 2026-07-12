#!/usr/bin/env python3
"""Gate ``server.json`` and ``CITATION.cff`` against the package version (#747).

Two version-bearing files had no CI gate and had both drifted: ``server.json``
(the MCP Registry manifest) and ``CITATION.cff``. README and SECURITY.md already
have gating drift checks (``check_readme_version.py`` / ``check_security_policy.py``);
this extends the same pattern to the last two.

Modes:

* ``--check`` (default): fail when ``server.json``'s ``version`` fields or
  ``CITATION.cff``'s ``version`` differ from the ``[project]`` version in
  ``pyproject.toml``. Wired into ``make ci`` and the ``publish.yml`` ``verify``
  job so a stale manifest can never be published.
* ``--sync``: rewrite ``server.json``'s version fields from ``pyproject.toml``
  (issue #747, 3-b — generate the manifest version at publish time). Run in
  ``publish.yml`` before the registry publish so the version is a single source
  of truth rather than a hand-edited field that lags releases.

``CITATION.cff``'s ``date-released`` is intentionally *not* synced: it is set by
the release process when a version is tagged and cannot be derived from
``pyproject.toml``. Only its ``version`` field is checked.

Intentionally stdlib-only (no ``tomllib`` — unavailable on Python 3.10; no
``contextweaver`` import) so it runs before the package is installed, matching
the ``check_readme_version.py`` convention.

Usage::

    python scripts/check_version_metadata.py            # drift check (exit 1 on drift)
    python scripts/check_version_metadata.py --sync      # rewrite server.json versions
"""

from __future__ import annotations

import re
import sys
from collections.abc import Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_PYPROJECT = REPO_ROOT / "pyproject.toml"
DEFAULT_SERVER_JSON = REPO_ROOT / "server.json"
DEFAULT_CITATION = REPO_ROOT / "CITATION.cff"

# The ``[project]`` table body: from the ``[project]`` header up to the next
# top-level table header (``[...]`` at column 0) or end of file.
_PROJECT_TABLE_RE = re.compile(r"^\[project\][^\n]*\n(.*?)(?=^\[|\Z)", re.MULTILINE | re.DOTALL)
_VERSION_RE = re.compile(r'^version = "([^"]+)"', re.MULTILINE)
# Every ``"version": "X"`` field in server.json (top-level + each package).
_SERVER_VERSION_RE = re.compile(r'("version":\s*)"([^"]*)"')
# ``version: X`` in CITATION.cff (YAML, column 0).
_CITATION_VERSION_RE = re.compile(r'^version:\s*"?([^"\n]+?)"?\s*$', re.MULTILINE)


def read_pyproject_version(pyproject: Path) -> str:
    """Return the ``[project]`` version string from *pyproject*."""
    text = pyproject.read_text(encoding="utf-8")
    table = _PROJECT_TABLE_RE.search(text)
    if table is None:
        raise ValueError(f"could not find a [project] table in {pyproject}")
    match = _VERSION_RE.search(table.group(1))
    if not match:
        raise ValueError(f"could not find a version in the [project] table of {pyproject}")
    return match.group(1)


def read_server_versions(server_json: Path) -> list[str]:
    """Return every ``version`` field value found in *server_json*."""
    text = server_json.read_text(encoding="utf-8")
    return [m.group(2) for m in _SERVER_VERSION_RE.finditer(text)]


def read_citation_version(citation: Path) -> str | None:
    """Return the ``version`` field from *citation*, or ``None`` if absent."""
    match = _CITATION_VERSION_RE.search(citation.read_text(encoding="utf-8"))
    return match.group(1) if match else None


def find_drift(version: str, server_json: Path, citation: Path) -> list[str]:
    """Return human-readable drift messages for the metadata files vs *version*."""
    problems: list[str] = []

    server_versions = read_server_versions(server_json)
    if not server_versions:
        problems.append(f"{server_json.name} has no 'version' field.")
    for found in server_versions:
        if found != version:
            problems.append(f"{server_json.name} version is {found!r}, expected {version!r}.")

    citation_version = read_citation_version(citation)
    if citation_version is None:
        problems.append(f"{citation.name} has no 'version' field.")
    elif citation_version != version:
        problems.append(f"{citation.name} version is {citation_version!r}, expected {version!r}.")

    return problems


def sync_server_json(version: str, server_json: Path) -> bool:
    """Rewrite every ``version`` field in *server_json* to *version*.

    Regex-replaces the version strings in place so all other formatting (key
    order, indentation, comments-as-fields) is preserved verbatim. Returns
    ``True`` if the file changed.
    """
    text = server_json.read_text(encoding="utf-8")
    new_text = _SERVER_VERSION_RE.sub(rf'\g<1>"{version}"', text)
    if new_text != text:
        server_json.write_text(new_text, encoding="utf-8")
        return True
    return False


def main(argv: Sequence[str] | None = None) -> int:
    """Check (default) or sync version metadata against ``pyproject.toml``."""
    args = list(sys.argv[1:] if argv is None else argv)
    version = read_pyproject_version(DEFAULT_PYPROJECT)

    if "--sync" in args:
        changed = sync_server_json(version, DEFAULT_SERVER_JSON)
        if changed:
            print(f"synced {DEFAULT_SERVER_JSON.name} version fields to {version}.")
        else:
            print(f"{DEFAULT_SERVER_JSON.name} version fields already at {version}.")
        return 0

    problems = find_drift(version, DEFAULT_SERVER_JSON, DEFAULT_CITATION)
    if problems:
        print(
            f"error: version metadata is out of date (pyproject is {version}):",
            file=sys.stderr,
        )
        for problem in problems:
            print(f"  - {problem}", file=sys.stderr)
        print(
            "Fix the stale values (or run this script with --sync for server.json) and re-run.",
            file=sys.stderr,
        )
        return 1
    print(f"server.json and CITATION.cff versions are in sync ({version}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())

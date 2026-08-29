#!/usr/bin/env python3
"""Prepare all version-coupled release artifacts from one target file.

This script exists to make the pre-release state atomic and reviewable.  It
updates package metadata, README current-version markers, the changelog,
release benchmark history/trend, and generated LLM documentation before a tag
is created.  The final validation reuses the same drift guards that gate CI and
publishing.

Typical usage::

    python benchmarks/benchmark.py
    python scripts/prepare_release.py --target-file .release-target.json

The target file is intentionally short-lived and may include release-specific
changelog text without teaching this script product history::

    {
      "version": "0.18.1",
      "date": "2026-08-10",
      "roadmap_highlights": "Release integrity and dependency automation fixes",
      "changelog": {
        "Fixed": ["..."],
        "Changed": ["..."]
      }
    }

On success the target file is removed so a bot-authored preparation commit does
not recursively trigger the release-branch workflow.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent
PYPROJECT = REPO_ROOT / "pyproject.toml"
README = REPO_ROOT / "README.md"
SERVER_JSON = REPO_ROOT / "server.json"
CITATION = REPO_ROOT / "CITATION.cff"
CHANGELOG = REPO_ROOT / "CHANGELOG.md"
LATEST_BENCHMARK = REPO_ROOT / "benchmarks" / "results" / "latest.json"

_PROJECT_TABLE_RE = re.compile(r"^\[project\][^\n]*\n(.*?)(?=^\[|\Z)", re.MULTILINE | re.DOTALL)
_VERSION_ASSIGN_RE = re.compile(r'^version = "([^"]+)"', re.MULTILINE)
_SEMVER_RE = re.compile(r"^(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)$")
_CITATION_VERSION_RE = re.compile(r'^version:\s*"?([^"\n]+?)"?\s*$', re.MULTILINE)
_CITATION_DATE_RE = re.compile(r"^date-released:\s*['\"]?([^'\"\n]+)['\"]?\s*$", re.MULTILINE)
_SERVER_VERSION_RE = re.compile(r'("version":\s*)"([^"]*)"')


def _read_project_version() -> str:
    text = PYPROJECT.read_text(encoding="utf-8")
    table = _PROJECT_TABLE_RE.search(text)
    if table is None:
        raise ValueError("pyproject.toml has no [project] table")
    match = _VERSION_ASSIGN_RE.search(table.group(1))
    if match is None:
        raise ValueError("pyproject.toml [project] table has no version")
    return match.group(1)


def _version_tuple(version: str) -> tuple[int, int, int]:
    match = _SEMVER_RE.fullmatch(version)
    if match is None:
        raise ValueError(f"release version must be strict MAJOR.MINOR.PATCH, got {version!r}")
    return tuple(int(group) for group in match.groups())  # type: ignore[return-value]


def _replace_once(text: str, old: str, new: str, *, label: str) -> str:
    count = text.count(old)
    if count != 1:
        raise ValueError(f"expected exactly one {label} occurrence, found {count}")
    return text.replace(old, new, 1)


def _update_pyproject(old: str, new: str) -> None:
    text = PYPROJECT.read_text(encoding="utf-8")
    table = _PROJECT_TABLE_RE.search(text)
    if table is None:
        raise ValueError("pyproject.toml has no [project] table")
    body = table.group(1)
    updated_body, count = _VERSION_ASSIGN_RE.subn(f'version = "{new}"', body, count=1)
    if count != 1 or old not in body:
        raise ValueError(f"could not replace [project] version {old!r}")
    text = text[: table.start(1)] + updated_body + text[table.end(1) :]
    PYPROJECT.write_text(text, encoding="utf-8")


def _update_readme(old: str, new: str, highlights: str) -> None:
    text = README.read_text(encoding="utf-8")
    current_pattern = re.compile(
        rf"(Current package version: \*\*){re.escape(old)}(\*\*\.?)"
    )
    text, current_count = current_pattern.subn(rf"\g<1>{new}\g<2>", text, count=1)
    if current_count != 1:
        raise ValueError(
            f"expected exactly one README current-version marker, found {current_count}"
        )

    comparison_replacements = (
        (
            f"(this repo, [v{old}]"
            f"(https://github.com/dgenio/contextweaver/releases/tag/v{old}))",
            f"(this repo, [v{new}]"
            f"(https://github.com/dgenio/contextweaver/releases/tag/v{new}))",
        ),
        (
            f"(this repo, [v{old}](https://pypi.org/project/contextweaver/{old}/))",
            f"(this repo, [v{new}](https://pypi.org/project/contextweaver/{new}/))",
        ),
    )
    matching_replacements = [
        (old_marker, new_marker)
        for old_marker, new_marker in comparison_replacements
        if old_marker in text
    ]
    if len(matching_replacements) != 1:
        raise ValueError(
            "expected exactly one supported README comparison self-reference, "
            f"found {len(matching_replacements)}"
        )
    compare_old, compare_new = matching_replacements[0]
    text = text.replace(compare_old, compare_new, 1)

    lines = text.splitlines(keepends=True)
    result: list[str] = []
    replaced_current_rows = 0
    for line in lines:
        marker = f"✅ current (v{old})"
        if line.startswith("| **v") and marker in line:
            completed = line.replace(marker, "✅ complete")
            result.append(completed)
            newline = "\n" if line.endswith("\n") else ""
            result.append(f"| **v{new}** | ✅ current (v{new}) | {highlights} |{newline}")
            replaced_current_rows += 1
        else:
            result.append(line)
    if replaced_current_rows == 0:
        raise ValueError(f"README contains no current roadmap row for v{old}")
    README.write_text("".join(result), encoding="utf-8")


def _update_server_json(version: str) -> None:
    text = SERVER_JSON.read_text(encoding="utf-8")
    updated, count = _SERVER_VERSION_RE.subn(rf'\g<1>"{version}"', text)
    if count == 0:
        raise ValueError("server.json contains no version fields")
    SERVER_JSON.write_text(updated, encoding="utf-8")


def _update_citation(version: str, release_date: str) -> None:
    text = CITATION.read_text(encoding="utf-8")
    text, version_count = _CITATION_VERSION_RE.subn(f'version: "{version}"', text, count=1)
    text, date_count = _CITATION_DATE_RE.subn(f"date-released: '{release_date}'", text, count=1)
    if version_count != 1 or date_count != 1:
        raise ValueError(
            "CITATION.cff must contain exactly one top-level version and date-released field"
        )
    CITATION.write_text(text, encoding="utf-8")


def _render_changelog_section(target: dict[str, Any]) -> str:
    version = str(target["version"])
    release_date = str(target["date"])
    changelog = target.get("changelog")
    if not isinstance(changelog, dict) or not changelog:
        raise ValueError("target changelog must be a non-empty object of heading -> bullet list")

    blocks = [f"## [{version}] - {release_date}\n"]
    for heading, bullets in changelog.items():
        if not isinstance(heading, str) or not heading.strip():
            raise ValueError("changelog heading must be a non-empty string")
        if not isinstance(bullets, list) or not bullets:
            raise ValueError(f"changelog section {heading!r} must contain at least one bullet")
        blocks.append(f"\n### {heading.strip()}\n\n")
        for bullet in bullets:
            if not isinstance(bullet, str) or not bullet.strip():
                raise ValueError(f"changelog section {heading!r} contains an invalid bullet")
            blocks.append(f"- {bullet.strip()}\n")
    return "".join(blocks) + "\n"


def _update_changelog(target: dict[str, Any]) -> None:
    version = str(target["version"])
    text = CHANGELOG.read_text(encoding="utf-8")
    if f"## [{version}]" in text:
        raise ValueError(f"CHANGELOG.md already contains a section for {version}")
    anchor = "## [Unreleased]\n"
    if text.count(anchor) != 1:
        raise ValueError("CHANGELOG.md must contain exactly one '## [Unreleased]' heading")
    section = _render_changelog_section(target)
    text = text.replace(anchor, anchor + "\n" + section, 1)
    CHANGELOG.write_text(text, encoding="utf-8")


def _run(*args: str) -> None:
    subprocess.run(args, cwd=REPO_ROOT, check=True)


def _generate_release_artifacts(version: str) -> None:
    if not LATEST_BENCHMARK.exists():
        raise ValueError(
            "benchmarks/results/latest.json is missing; "
            "run the benchmark before preparing a release"
        )
    _run(
        sys.executable,
        "scripts/render_trend.py",
        "--snapshot",
        version,
        "--from",
        "benchmarks/results/latest.json",
    )
    _run(sys.executable, "scripts/render_trend.py")
    _run(sys.executable, "scripts/gen_llms.py")


def _validate() -> None:
    for script in (
        "scripts/check_readme_version.py",
        "scripts/check_security_policy.py",
        "scripts/check_version_metadata.py",
        "scripts/check_release_readiness.py",
    ):
        _run(sys.executable, script)
    _run(sys.executable, "scripts/render_trend.py", "--check")
    _run(sys.executable, "scripts/gen_llms.py", "--check")


def _load_target(path: Path) -> dict[str, Any]:
    target = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(target, dict):
        raise ValueError("release target must be a JSON object")
    for key in ("version", "date", "roadmap_highlights", "changelog"):
        if key not in target:
            raise ValueError(f"release target is missing required field {key!r}")
    return target


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-file", type=Path, default=Path(".release-target.json"))
    args = parser.parse_args()

    target_path = args.target_file
    if not target_path.is_absolute():
        target_path = REPO_ROOT / target_path
    target = _load_target(target_path)
    new_version = str(target["version"])
    release_date = str(target["date"])
    highlights = str(target["roadmap_highlights"]).strip()
    if not highlights:
        raise ValueError("roadmap_highlights must not be empty")

    old_version = _read_project_version()
    old_tuple = _version_tuple(old_version)
    new_tuple = _version_tuple(new_version)
    if new_tuple <= old_tuple:
        raise ValueError(f"release version {new_version} must be newer than {old_version}")

    _update_pyproject(old_version, new_version)
    _update_readme(old_version, new_version, highlights)
    _update_server_json(new_version)
    _update_citation(new_version, release_date)
    _update_changelog(target)
    _generate_release_artifacts(new_version)
    _validate()

    target_path.unlink()
    print(f"prepared contextweaver {new_version} release artifacts successfully")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

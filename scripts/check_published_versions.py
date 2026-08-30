#!/usr/bin/env python3
"""Reconcile released ``vX.Y.Z`` tags against what PyPI actually serves.

``check_release_readiness.py`` guards the version *about to be* released. This
guards the ones already out: it catches a release that was cut, tagged and
announced on GitHub but never reached PyPI.

That is not hypothetical. Between 0.16.0 (2026-06-23) and 0.18.2 (2026-08-29),
three tagged releases -- 0.17.0, 0.18.0 and 0.18.1 -- were advertised on GitHub
while PyPI had none of them, for up to six weeks, with nothing reporting it.
``pip install contextweaver==0.18.0`` failed for anyone who read the release
notes. Each had a different cause (see ``release/unpublished_versions.json``),
which is the point: the failure mode is not one bug, it is the absence of a
check that the two lists agree.

Known-and-accepted gaps live in ``release/unpublished_versions.json`` with a
reason each. This script fails when a tag is missing from PyPI and is *not*
listed there, and also when a listed version *is* on PyPI -- a stale exemption
would mask the next real gap.

Intentionally stdlib-only, matching ``check_readme_version.py`` and
``check_version_metadata.py``, so it runs before the package is installed.

Network: reads the PyPI JSON API. It is therefore wired into
``release-readiness.yml`` on push/schedule, and deliberately NOT into
``make ci`` -- a local gate must not depend on pypi.org being reachable.

Usage::

    python scripts/check_published_versions.py              # exit 1 on divergence
    python scripts/check_published_versions.py --json       # machine-readable
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_EXEMPTIONS = REPO_ROOT / "release" / "unpublished_versions.json"

PACKAGE = "contextweaver"
PYPI_JSON = f"https://pypi.org/pypi/{PACKAGE}/json"
TAG_PATTERN = re.compile(r"^v(\d+\.\d+\.\d+)$")

Fetcher = Callable[[str], bytes]


def _urlopen(url: str) -> bytes:
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310 - fixed https URL
        return bytes(response.read())


def released_tags(root: Path = REPO_ROOT) -> set[str]:
    """Versions with a ``vX.Y.Z`` tag in the local clone.

    Requires tags to be present: CI must check out with ``fetch-tags``, or this
    returns an empty set and the caller treats that as an error rather than as
    "nothing to reconcile".
    """
    result = subprocess.run(
        ["git", "tag", "--list", "v*"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    versions = set()
    for line in result.stdout.splitlines():
        match = TAG_PATTERN.match(line.strip())
        if match is not None:
            versions.add(match.group(1))
    return versions


def published_versions(fetch: Fetcher = _urlopen) -> set[str]:
    """Versions PyPI serves at least one file for.

    A release with an empty file list (every file yanked/removed) is *not*
    installable, so it does not count as published.
    """
    payload = json.loads(fetch(PYPI_JSON))
    return {version for version, files in payload["releases"].items() if files}


def load_exemptions(path: Path = DEFAULT_EXEMPTIONS) -> dict[str, str]:
    if not path.exists():
        return {}
    raw = json.loads(path.read_text(encoding="utf-8"))
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def untagged_published(tags: set[str], published: set[str]) -> set[str]:
    """Published versions with no local tag — the sign of a partial checkout.

    A *truncated* tag list is more dangerous than an empty one: it reconciles
    the handful of tags that happen to be present, passes, and reports a fact
    about the clone rather than about the project. That is not hypothetical
    either — this check was written and validated in a clone holding 4 of the
    repository's 38 tags, so the eleven gaps below 0.14.0 stayed invisible
    until it first ran in CI, where it went red immediately.

    Every version PyPI serves for this package was cut from a tag here, so a
    published version with no local tag means the tags are incomplete.
    """
    return published - tags


def reconcile(
    tags: set[str],
    published: set[str],
    exemptions: dict[str, str],
) -> tuple[set[str], set[str]]:
    """Return (unexplained gaps, stale exemptions)."""
    missing = tags - published
    return missing - set(exemptions), set(exemptions) & published


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="emit a machine-readable report")
    args = parser.parse_args(argv)

    tags = released_tags()
    if not tags:
        print(
            "error: no vX.Y.Z tags found. Check out with tags "
            "(actions/checkout `fetch-tags: true`) — an empty tag list would "
            "otherwise pass this check while reconciling nothing.",
            file=sys.stderr,
        )
        return 1

    try:
        published = published_versions()
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        print(f"error: could not read {PYPI_JSON}: {error}", file=sys.stderr)
        return 1

    untagged = untagged_published(tags, published)
    if untagged:
        print(
            f"error: PyPI serves {len(untagged)} version(s) with no tag in this "
            f"checkout ({', '.join(sorted(untagged)[:5])}"
            f"{', ...' if len(untagged) > 5 else ''}). The tag list is partial, so "
            "reconciling it would report a fact about this clone, not about the "
            "project. Fetch tags (`git fetch --tags`, or actions/checkout "
            "`fetch-tags: true`) and re-run.",
            file=sys.stderr,
        )
        return 1

    exemptions = load_exemptions()
    unexplained, stale = reconcile(tags, published, exemptions)

    if args.json:
        print(
            json.dumps(
                {
                    "tags": sorted(tags),
                    "published": sorted(published),
                    "unexplained_gaps": sorted(unexplained),
                    "stale_exemptions": sorted(stale),
                },
                indent=2,
            )
        )

    for version in sorted(unexplained):
        print(
            f"error: v{version} is tagged but absent from PyPI. Either publish it, "
            f"or record why not in {DEFAULT_EXEMPTIONS.relative_to(REPO_ROOT)}.",
            file=sys.stderr,
        )
    for version in sorted(stale):
        print(
            f"error: {version} is listed as unpublished but PyPI now serves it. "
            f"Remove its entry from {DEFAULT_EXEMPTIONS.relative_to(REPO_ROOT)} — "
            "a stale exemption hides the next real gap.",
            file=sys.stderr,
        )

    if unexplained or stale:
        return 1

    accepted = f" ({len(exemptions)} recorded as deliberately unpublished)" if exemptions else ""
    print(f"published versions: OK — {len(tags)} tag(s) reconciled with PyPI{accepted}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

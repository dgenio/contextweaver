#!/usr/bin/env python3
"""Fail when a tracked file claims weaver-spec.dev serves schemas (#848).

``https://weaver-spec.dev/contracts/v0/...`` is the canonical ``$id`` namespace
weaver-spec *reserves*. It is not a live endpoint: as of 2026-09-07 the domain
does not resolve at all, and every schema this repository validates against is
fetched from ``raw.githubusercontent.com/dgenio/weaver-spec/<tag>/contracts/json/``
at a pinned tag (``WEAVER_SPEC_REF``, issue #757). Live immutable hosting is
tracked upstream in ``dgenio/weaver-spec#213``.

The claim had already drifted into two places and been corrected in one of them
(PR #852 fixed ``docs/weaver_spec_mapping.md``), while the CI workflow went on
describing the conformance gate as validating "against the canonical contracts
published at" that address. Nothing read the prose, so nothing caught it. This
does.

**What this checks and what it does not.** It is a *claim* check, not a liveness
check: it never touches the network, so it cannot tell you whether the endpoint
came up. It fails when a tracked file uses the canonical host in a sentence that
asserts the schemas are served there — "published at", "fetched from", "hosted
at", "available at", "live at", and so on. Mentioning the domain is fine, and
has to be: ``docs/weaver_spec_mapping.md`` explains the namespace at length, and
this file names it in every other line.

**When upstream #213 lands**, this script is what should be replaced -- by a
real liveness + hash check against the endpoint (issue #848, acceptance
criterion 5) -- rather than deleted, so the restored claim is backed by a
verification instead of by someone's recollection.

Intentionally stdlib-only and import-free so it runs before the package is
installed, matching ``check_version_metadata.py`` and ``check_security_policy.py``.

Usage::

    python scripts/check_schema_hosting_claims.py          # exit 1 on a live-hosting claim
    python scripts/check_schema_hosting_claims.py --list    # show every mention, claim or not
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent

CANONICAL_HOST = "weaver-spec.dev"

# Verbs that turn a mention of the host into an assertion that it serves the
# schemas. Matched within a window around the host so an ordinary reference --
# "reserves ... as the canonical $id namespace" -- does not trip the gate.
_SERVING_VERBS = (
    "published",
    "publishes",
    "hosted",
    "hosts",
    "served",
    "serves",
    "fetched",
    "fetches",
    "downloaded",
    "available",
    "live",
    "resolves",
)

# Characters of context to inspect either side of the host mention. One
# sentence's worth: wide enough to catch "published at <host>" split across a
# comment's line wrap, narrow enough not to swallow the next paragraph.
_WINDOW = 120

# Matched as whole words (or exact phrases) rather than substrings, and only
# against the clause the host appears in -- see ``claims_in``. "yet" and
# "would" were in this list and are deliberately gone: as bare substrings they
# swallowed genuine claims ("published at <host>, yet also mirrored
# elsewhere"), and they are redundant, since the wording they existed for --
# "not yet a live schema endpoint" -- is already caught by "not".
_NEGATIONS = (
    "not",
    "never",
    "no longer",
    "isn't",
    "cannot",
    "must not",
    "used to",
    "reserved",
    "reserves",
    "would be",
    "will be",
)

_NEGATION_RE = re.compile(r"(?<!\w)(" + "|".join(re.escape(n) for n in _NEGATIONS) + r")(?!\w)")


def tracked_files() -> list[Path]:
    """Every file git tracks, as repo-relative paths."""
    out = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    return [REPO_ROOT / name for name in out.split("\0") if name]


# Case-insensitive throughout. A hostname is case-insensitive by RFC 4343, so
# `Weaver-Spec.dev` in prose addresses the same host -- and the first version
# of this gate matched it case-sensitively, which meant a single capital
# bypassed the check entirely. Found by Copilot on #879 as a *suppressed*
# review comment (not a thread), and missed at merge time because only the
# threads were read.
_HOST_RE = re.compile(re.escape(CANONICAL_HOST), re.IGNORECASE)


def _windows(text: str) -> list[tuple[int, str]]:
    """Return ``(line_number, context)`` for every mention of the host."""
    found: list[tuple[int, str]] = []
    for match in _HOST_RE.finditer(text):
        start = max(0, match.start() - _WINDOW)
        end = min(len(text), match.end() + _WINDOW)
        line_no = text.count("\n", 0, match.start()) + 1
        found.append((line_no, text[start:end]))
    return found


def _clause_around_host(lowered: str) -> str:
    """Return just the sentence/clause containing the host mention.

    The surrounding window is a sentence's worth either side, which is right
    for catching a claim split across a line wrap and wrong for deciding
    whether that claim was negated -- "the host does not serve these. They are
    published at <host>." must fail, and does, because only the second
    sentence is inspected.
    """
    # *lowered* is already case-folded by the caller, so a plain find is
    # correct here and matches whatever casing the source used.
    where = lowered.find(CANONICAL_HOST)
    if where == -1:
        return lowered
    start = max(lowered.rfind(sep, 0, where) + 1 for sep in (". ", "; ", "! ", "? "))
    end_candidates = [lowered.find(sep, where) for sep in (". ", "; ", "! ", "? ")]
    ends = [e for e in end_candidates if e != -1]
    end = min(ends) if ends else len(lowered)
    return lowered[start:end].strip()


# A clause naming this script is describing the gate, not making the claim --
# the CI step and the Makefile target both have to say what they check. Found
# by this module's own test suite the moment the negation matching was
# tightened: the guard flagged its own wiring comments.
_SELF_REFERENCE = "check_schema_hosting_claims"

# This module's own test file must contain example claims -- that is what makes
# it a test. Most cases build them from a placeholder, but the casing cases
# have to spell the host out literally, so they trip the gate. Exempted by
# path, narrowly: a claim inside a test fixture is not a public claim, and the
# exemption is pinned by ``test_the_exempt_set_is_exactly_two_files`` so it
# cannot silently widen to, say, all of ``tests/``.
_EXEMPT_PATHS = frozenset(
    {
        Path(__file__).resolve(),
        REPO_ROOT / "tests" / "test_check_schema_hosting_claims.py",
    }
)


def claims_in(path: Path) -> list[tuple[int, str]]:
    """Return ``(line, context)`` for each live-hosting claim in *path*."""
    if path.resolve() in _EXEMPT_PATHS:
        return []
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return []
    if not _HOST_RE.search(text):
        return []

    claims: list[tuple[int, str]] = []
    for line_no, context in _windows(text):
        lowered = " ".join(context.lower().split())
        clause = _clause_around_host(lowered)
        if not any(verb in clause for verb in _SERVING_VERBS):
            continue
        # Negation is checked against the clause, not the whole window: a
        # denial in a neighbouring sentence must not excuse a claim here.
        if _NEGATION_RE.search(clause):
            continue
        if _SELF_REFERENCE in clause:
            continue
        claims.append((line_no, clause))
    return claims


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)

    if "--list" in args:
        for path in tracked_files():
            try:
                text = path.read_text(encoding="utf-8")
            except (UnicodeDecodeError, OSError):
                continue
            for line_no, _ in _windows(text):
                rel = path.relative_to(REPO_ROOT)
                print(f"{rel}:{line_no}")
        return 0

    failures: list[str] = []
    for path in tracked_files():
        for line_no, context in claims_in(path):
            rel = path.relative_to(REPO_ROOT)
            failures.append(f"  {rel}:{line_no}\n    ...{context}...")

    if failures:
        print(
            f"{CANONICAL_HOST} is a reserved $id namespace, not a live schema endpoint\n"
            "(it does not resolve). These files say otherwise:\n",
            file=sys.stderr,
        )
        print("\n".join(failures), file=sys.stderr)
        print(
            "\nSchemas are fetched from raw.githubusercontent.com at the pinned\n"
            "WEAVER_SPEC_REF tag. Live hosting is tracked in dgenio/weaver-spec#213;\n"
            "restore the claim only alongside a liveness + hash check (issue #848).",
            file=sys.stderr,
        )
        return 1

    print(
        f"No tracked file claims {CANONICAL_HOST} serves schemas "
        "(reserved $id namespace, not a live endpoint)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

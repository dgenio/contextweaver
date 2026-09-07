"""Tests for the weaver-spec.dev hosting-claim guard (#848).

The guard exists because a claim drifted into two files, was corrected in one
(#852) and left in the other -- ``ci.yml`` went on describing the conformance
gate as validating "against the canonical contracts published at" a domain that
does not resolve and that the step has never fetched from. Nothing read the
prose, so nothing caught it.

It has to thread a needle: fail on a sentence asserting the schemas are served
there, while coexisting with ``docs/weaver_spec_mapping.md``, whose whole job
is explaining that namespace at length. These tests pin both sides of that, and
the repository's real state.

The guard lives under ``scripts/``, so it is added to ``sys.path`` the same way
:mod:`tests.test_check_security_policy` does.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "scripts"))

import check_schema_hosting_claims  # noqa: E402  (import after sys.path manipulation)

_HOST = "https://weaver-spec.dev/contracts/v0/"

# (text, expected_to_fail, why)
_CASES = [
    (
        f"Schemas are published at {_HOST} today.",
        True,
        "the plain claim the guard exists for",
    ),
    (
        f"Contracts are hosted at {_HOST}.",
        True,
        "a serving verb other than 'published'",
    ),
    (
        "Schemas are published at https://Weaver-Spec.dev/contracts/v0/ today.",
        True,
        "mixed-case host -- hostnames are case-insensitive (RFC 4343), and the "
        "first version of this gate let a single capital bypass it entirely",
    ),
    (
        "Schemas are published at HTTPS://WEAVER-SPEC.DEV/CONTRACTS/V0/ today.",
        True,
        "upper-case host",
    ),
    (
        f"published at {_HOST}, yet also mirrored elsewhere.",
        True,
        "'yet' is not a negation -- it was a substring in an early draft of "
        "_NEGATIONS and swallowed genuine claims",
    ),
    (
        f"The host does not serve these. They are published at {_HOST} now.",
        True,
        "a denial in a NEIGHBOURING sentence must not excuse the claim -- "
        "negation is scoped to the clause holding the host",
    ),
    (
        "weaver-spec reserves https://weaver-spec.dev/contracts/v0/... as the "
        "canonical `$id` namespace, but the canonical host is not yet a live "
        "schema endpoint.",
        False,
        "the real docs/weaver_spec_mapping.md wording must keep passing",
    ),
    (
        f"The schemas would be published at {_HOST} one day.",
        False,
        "a hypothetical is not a claim",
    ),
    (
        "https://weaver-spec.dev/ is never used as a fetch target.",
        False,
        "a genuine denial",
    ),
    (
        "See https://weaver-spec.dev/ for the namespace definition.",
        False,
        "a bare mention with no serving verb",
    ),
]


@pytest.mark.parametrize(
    ("text", "expected_failure", "why"),
    _CASES,
    ids=[c[2][:48] for c in _CASES],
)
def test_claim_detection(tmp_path: Path, text: str, expected_failure: bool, why: str) -> None:
    doc = tmp_path / "doc.md"
    doc.write_text(text, encoding="utf-8")
    assert bool(check_schema_hosting_claims.claims_in(doc)) is expected_failure, why


def test_a_clause_naming_the_guard_is_not_a_claim(tmp_path: Path) -> None:
    """The CI step and Makefile target must be able to say what they check.

    Tightening the negation matching made the guard flag its own wiring
    comments; this pins the exemption so the next tightening cannot.
    """
    doc = tmp_path / "wiring.yml"
    doc.write_text(
        "# check_schema_hosting_claims.py fails when a file says "
        "weaver-spec.dev serves the schemas.",
        encoding="utf-8",
    )
    assert check_schema_hosting_claims.claims_in(doc) == []


def test_the_guard_ignores_itself() -> None:
    """The script names the host in nearly every other line of its docstring."""
    script = Path(check_schema_hosting_claims.__file__).resolve()
    assert check_schema_hosting_claims.claims_in(script) == []


def test_the_exempt_set_is_exactly_two_files() -> None:
    """The guard and its own test file, and nothing else.

    Both must hold example claims to do their jobs. Pinned as a set so the
    exemption cannot quietly grow into "all of tests/", which would let a real
    claim hide in a fixture.
    """
    # Repo-relative, not basename: two files can share a name, and comparing
    # names would let a second "test_check_schema_hosting_claims.py" elsewhere
    # in the tree join the exempt set unnoticed.
    root = Path(check_schema_hosting_claims.REPO_ROOT).resolve()
    exempt = {p.relative_to(root).as_posix() for p in check_schema_hosting_claims._EXEMPT_PATHS}
    assert exempt == {
        "scripts/check_schema_hosting_claims.py",
        "tests/test_check_schema_hosting_claims.py",
    }


def test_the_repository_is_currently_clean() -> None:
    """The real tree, which is what CI runs this against."""
    assert check_schema_hosting_claims.main([]) == 0


def test_a_binary_or_unreadable_file_is_skipped_not_fatal(tmp_path: Path) -> None:
    """`git ls-files` includes non-text files; the guard walks all of them."""
    blob = tmp_path / "image.png"
    blob.write_bytes(b"\x89PNG\r\n\x1a\n\xff\xfe not utf-8 \xff")
    assert check_schema_hosting_claims.claims_in(blob) == []

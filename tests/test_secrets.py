"""Tests for contextweaver.secrets (issue #428)."""

from __future__ import annotations

from contextweaver.secrets import (
    DEFAULT_SECRET_MASK,
    contains_secret,
    scrub_secrets,
    scrub_secrets_in_list,
)

# A representative sample per built-in pattern.  These are synthetic fixtures
# assembled from fragments on purpose: the literal strings must match the
# detection *shapes* without ever appearing verbatim in source (otherwise
# secret-scanning push protection blocks the commit).
AWS_KEY = "AKIA" + "A" * 16  # AKIA + exactly 16 chars
GITHUB_TOKEN = "ghp_" + "a" * 36
SLACK_TOKEN = "xoxb-" + "0" * 12 + "-" + "x" * 16
GOOGLE_KEY = "AIza" + "B" * 35
JWT = "eyJ" + "abcDEF123" * 2 + ".eyJ" + "ghiJKL456" * 2 + "." + "mnoPQR789" * 2


def test_aws_access_key_is_masked() -> None:
    out = scrub_secrets(f"key = {AWS_KEY} done")
    assert AWS_KEY not in out
    assert DEFAULT_SECRET_MASK in out
    assert out.startswith("key = ") and out.endswith(" done")


def test_github_slack_google_jwt_masked() -> None:
    for secret in (GITHUB_TOKEN, SLACK_TOKEN, GOOGLE_KEY, JWT):
        out = scrub_secrets(f"token: {secret}")
        assert secret not in out
        assert DEFAULT_SECRET_MASK in out


def test_private_key_block_masked() -> None:
    block = (
        "-----BEGIN RSA PRIVATE KEY-----\n"
        "MIIBOgIBAAJBAKj34GkxFhD90vcNLYLInFEX6Ppy1tPf9Cnzj4p4WGeKLs1Pt8Q\n"
        "-----END RSA PRIVATE KEY-----"
    )
    out = scrub_secrets(f"here:\n{block}\nafter")
    assert "PRIVATE KEY" not in out
    assert DEFAULT_SECRET_MASK in out
    assert out.startswith("here:\n") and out.endswith("\nafter")


def test_credential_assignment_masks_value_keeps_key() -> None:
    out = scrub_secrets("aws_secret_access_key=wJalrXUtnFEMI1K7MDENGbPxRfiCYEXAMPLEKEY")
    assert "wJalrXUtnFEMI1K7MDENGbPxRfiCYEXAMPLEKEY" not in out
    # The key name is preserved so the redacted surface stays readable.
    assert out.startswith("aws_secret_access_key=")
    assert DEFAULT_SECRET_MASK in out


def test_url_credentials_mask_password_only() -> None:
    out = scrub_secrets("postgres://admin:s3cr3tP@ss@db.example.com:5432/app")
    assert "s3cr3tP@ss" not in out
    assert "postgres://admin:" in out
    assert "@db.example.com:5432/app" in out


def test_bearer_token_masked() -> None:
    out = scrub_secrets("Authorization: Bearer abcDEF123456ghiJKL")
    assert "abcDEF123456ghiJKL" not in out
    assert DEFAULT_SECRET_MASK in out


def test_clean_text_unchanged() -> None:
    text = "The invoice total was 42 dollars and the job completed successfully."
    assert scrub_secrets(text) == text
    assert not contains_secret(text)


def test_empty_text_is_noop() -> None:
    assert scrub_secrets("") == ""
    assert not contains_secret("")


def test_custom_mask() -> None:
    out = scrub_secrets(f"k={AWS_KEY}", mask="***")
    assert "***" in out
    assert AWS_KEY not in out


def test_contains_secret_detects_and_rejects() -> None:
    assert contains_secret(f"token {GITHUB_TOKEN}")
    assert not contains_secret("nothing sensitive here")


def test_scrub_list_preserves_order_and_length() -> None:
    facts = ["total=42", f"key={AWS_KEY}", "status=ok"]
    out = scrub_secrets_in_list(facts)
    assert len(out) == 3
    assert out[0] == "total=42"
    assert AWS_KEY not in out[1]
    assert out[2] == "status=ok"


def test_scrub_is_deterministic() -> None:
    text = f"a={AWS_KEY} b={GITHUB_TOKEN}"
    assert scrub_secrets(text) == scrub_secrets(text)

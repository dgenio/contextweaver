"""Deterministic secret detection and scrubbing primitives (issue #428).

This module is a **pure, side-effect-free** utility — like
:mod:`contextweaver._utils` it performs only in-memory computation (regex
matching) with no I/O, so it can be shared across the layered architecture:
the Context Engine firewall scrubs summaries and extracted facts with it, the
opt-in :class:`~contextweaver.context.secret_redaction.SecretRedactor`
:class:`~contextweaver.protocols.RedactionHook` wraps it, the ingestion
sensitivity classifier reuses :func:`contains_secret` as a heuristic signal,
and the Routing Engine card builder scrubs :class:`ChoiceCard` text with it.

Detection is **deterministic and pattern-based** — no model, no randomness, no
entropy thresholds that would vary by input encoding — so a scrubbed surface is
reproducible and auditable, matching the project's no-LLM-in-the-loop guarantee.

The patterns target *well-known secret shapes* (cloud access keys, AI-provider
and SaaS API keys — OpenAI/Anthropic ``sk-`` families, GitHub PATs, Slack,
Stripe, SendGrid — private-key blocks, JWTs, credential-bearing connection
strings, and ``key = value`` assignments for credential-named keys).  Detection is
intentionally conservative: it never claims to find *every* secret, and the
scrubber only ever *removes* characters — it can tighten a surface but never
widen or weaken it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Default replacement text substituted for a detected secret.  ASCII so it
#: never perturbs token estimates that assume single-byte characters.
DEFAULT_SECRET_MASK = "[REDACTED-SECRET]"


@dataclass(frozen=True)
class SecretPattern:
    """A single named secret-detection rule.

    Attributes:
        name: Short identifier for the rule (surfaced nowhere user-facing by
            default; useful for tests and debugging).
        pattern: Compiled regular expression.  When it defines a named group
            ``secret`` only that group is masked (the surrounding context — e.g.
            the ``aws_secret_access_key=`` prefix — is preserved so the redacted
            surface stays readable); otherwise the whole match is masked.
    """

    name: str
    pattern: re.Pattern[str]


# ---------------------------------------------------------------------------
# Built-in patterns
# ---------------------------------------------------------------------------
#
# Ordering matters only for overlapping matches; non-overlapping rules are
# independent.  Each rule either masks its whole match (standalone token
# shapes) or a named ``secret`` group (assignment / credential-in-context
# shapes) so surrounding context survives.

_DEFAULT_PATTERNS: tuple[SecretPattern, ...] = (
    # PEM / OpenSSH private key blocks (multi-line; masked whole).
    SecretPattern(
        "private_key_block",
        re.compile(
            r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY-----"
            r".*?-----END (?:RSA |EC |DSA |OPENSSH |PGP |ENCRYPTED )?PRIVATE KEY-----",
            re.DOTALL,
        ),
    ),
    # AWS access key id (AKIA/ASIA/AGPA/... + 16 base32 chars).
    SecretPattern(
        "aws_access_key_id",
        re.compile(r"\b(?:A3T[A-Z0-9]|AKIA|AGPA|AIDA|AROA|AIPA|ANPA|ANVA|ASIA)[A-Z0-9]{16}\b"),
    ),
    # Google API key.
    SecretPattern("google_api_key", re.compile(r"\bAIza[0-9A-Za-z_\-]{35}\b")),
    # GitHub personal-access / OAuth / app tokens.
    SecretPattern("github_token", re.compile(r"\bgh[pousr]_[A-Za-z0-9]{36,}\b")),
    # Slack tokens (user/bot/workspace).
    SecretPattern("slack_token", re.compile(r"\bxox[baprs]-[A-Za-z0-9-]{10,}\b")),
    # ---- AI-provider and modern SaaS token shapes (issue #742) ----
    # Anthropic API keys (``sk-ant-...``).  Matched before the generic OpenAI
    # rule so the ``ant-`` family gets its own named detection.
    SecretPattern("anthropic_api_key", re.compile(r"\bsk-ant-[A-Za-z0-9_\-]{20,}")),
    # OpenAI API keys (``sk-...`` / ``sk-proj-...``).  The negative lookahead
    # keeps this from double-claiming the Anthropic shape above.
    SecretPattern("openai_api_key", re.compile(r"\bsk-(?!ant-)(?:proj-)?[A-Za-z0-9_\-]{20,}")),
    # GitHub fine-grained PATs (``github_pat_...``); the legacy ``gh[pousr]_``
    # shapes are covered by ``github_token`` above.
    SecretPattern("github_fine_grained_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{22,}")),
    # Slack app-level tokens (``xapp-1-...``); distinct prefix from ``xox*``.
    SecretPattern("slack_app_token", re.compile(r"\bxapp-[0-9]-[A-Za-z0-9-]{10,}")),
    # Stripe live secret / restricted keys (``sk_live_`` / ``rk_live_``).
    SecretPattern("stripe_key", re.compile(r"\b(?:sk|rk)_live_[A-Za-z0-9]{16,}")),
    # SendGrid API keys (``SG.<22>.<43>``).
    SecretPattern(
        "sendgrid_key",
        re.compile(r"\bSG\.[A-Za-z0-9_\-]{16,}\.[A-Za-z0-9_\-]{16,}"),
    ),
    # JSON Web Token (header.payload.signature).
    SecretPattern(
        "jwt",
        re.compile(r"\beyJ[A-Za-z0-9_\-]+\.eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\b"),
    ),
    # Credentials embedded in a URL / connection string (``scheme://user:pass@``).
    # Only the password component is masked.  The password is matched greedily up
    # to the *last* ``@`` before a host-shaped segment, so a raw (unescaped) ``@``
    # inside the password is masked whole rather than leaking its suffix.
    SecretPattern(
        "url_credentials",
        re.compile(
            r"(?P<prefix>[a-zA-Z][a-zA-Z0-9+.\-]*://[^\s:/@]+:)"
            r"(?P<secret>[^\s/]+)(?=@[^\s:/@]+)"
        ),
    ),
    # Bearer / authorization tokens.
    SecretPattern(
        "bearer_token",
        re.compile(r"(?i)(?P<prefix>\bbearer\s+)(?P<secret>[A-Za-z0-9._\-]{10,})"),
    ),
    # Generic ``key = value`` / ``key: value`` assignments for credential-named
    # keys.  The key name is preserved; only the value is masked.
    SecretPattern(
        "credential_assignment",
        re.compile(
            r"(?i)(?P<prefix>\b[\w.\-]*"
            r"(?:api[_-]?key|secret|token|password|passwd|pwd|access[_-]?key)"
            r"[\w.\-]*\s*[=:]\s*)"
            r"(?P<secret>['\"]?[^\s'\"]{6,}['\"]?)"
        ),
    ),
)


def _mask_match(match: re.Match[str], mask: str) -> str:
    """Return the replacement for *match*, masking only the ``secret`` group when present."""
    groups = match.groupdict()
    if "secret" in groups and groups["secret"] is not None:
        prefix = groups.get("prefix") or ""
        return f"{prefix}{mask}"
    return mask


def scrub_secrets(
    text: str,
    *,
    mask: str = DEFAULT_SECRET_MASK,
    patterns: tuple[SecretPattern, ...] = _DEFAULT_PATTERNS,
) -> str:
    """Return *text* with well-known secret shapes replaced by *mask*.

    Deterministic: the same input always yields the same output.  Only removes
    characters — never adds caller content — so it can only tighten a surface.

    Args:
        text: The text to scrub.
        mask: Replacement string for each detected secret.  Defaults to
            :data:`DEFAULT_SECRET_MASK`.
        patterns: Detection rules to apply.  Defaults to the built-in
            :data:`_DEFAULT_PATTERNS`; pass a custom tuple to extend or restrict.

    Returns:
        The scrubbed text.  Returns the input unchanged when no pattern matches.
    """
    if not text:
        return text
    scrubbed = text
    for rule in patterns:
        scrubbed = rule.pattern.sub(lambda m: _mask_match(m, mask), scrubbed)
    return scrubbed


def scrub_secrets_in_list(
    items: list[str],
    *,
    mask: str = DEFAULT_SECRET_MASK,
    patterns: tuple[SecretPattern, ...] = _DEFAULT_PATTERNS,
) -> list[str]:
    """Apply :func:`scrub_secrets` to each string in *items* (order preserved)."""
    return [scrub_secrets(item, mask=mask, patterns=patterns) for item in items]


def scrub_secrets_in_obj(
    obj: object,
    *,
    mask: str = DEFAULT_SECRET_MASK,
    patterns: tuple[SecretPattern, ...] = _DEFAULT_PATTERNS,
) -> object:
    """Recursively scrub secret shapes in the string leaves of *obj*.

    Walks ``dict`` / ``list`` containers and applies :func:`scrub_secrets` to
    every ``str`` leaf, leaving non-string scalars and the overall shape
    (keys, nesting, list length) untouched.  Used by the firewall facade to
    scrub schema-preserving pass-through payloads without changing their shape
    (issue #745).  Deterministic; only ever removes characters.

    Args:
        obj: An arbitrary JSON-like value (dict / list / str / scalar).
        mask: Replacement string for each detected secret.
        patterns: Detection rules to apply.

    Returns:
        A structurally identical value with secret shapes masked in strings.
    """
    if isinstance(obj, str):
        return scrub_secrets(obj, mask=mask, patterns=patterns)
    if isinstance(obj, list):
        return [scrub_secrets_in_obj(item, mask=mask, patterns=patterns) for item in obj]
    if isinstance(obj, dict):
        return {
            key: scrub_secrets_in_obj(value, mask=mask, patterns=patterns)
            for key, value in obj.items()
        }
    return obj


def contains_secret(
    text: str,
    *,
    patterns: tuple[SecretPattern, ...] = _DEFAULT_PATTERNS,
) -> bool:
    """Return ``True`` when *text* matches any secret-detection rule.

    Used by the ingestion sensitivity classifier (issue #542) as a deterministic
    signal that an item carries credential-shaped content and should be labelled
    at least ``restricted``.
    """
    if not text:
        return False
    return any(rule.pattern.search(text) for rule in patterns)


__all__ = [
    "DEFAULT_SECRET_MASK",
    "SecretPattern",
    "contains_secret",
    "scrub_secrets",
    "scrub_secrets_in_list",
    "scrub_secrets_in_obj",
]

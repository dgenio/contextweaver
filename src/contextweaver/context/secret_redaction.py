"""Opt-in deterministic secret-redaction hook for the Context Engine (issue #428).

The firewall keeps raw tool results out of the prompt, but the *summary* that
does reach the prompt — and any facts extracted from it — can still carry
embedded secrets (API keys, tokens, connection strings) copied verbatim from the
raw payload.  :class:`SecretRedactor` is a
:class:`~contextweaver.protocols.RedactionHook` that scrubs well-known secret
shapes from an item's text using the deterministic
:func:`contextweaver.secrets.scrub_secrets` primitive.

Unlike :class:`~contextweaver.context.sensitivity.MaskRedactionHook` (which masks
an item's *entire* text when it meets the sensitivity floor), this hook performs
a *substring* scrub: non-secret text is preserved so the summary stays useful.
It is registered under the name ``"secret"`` so it can be referenced from
:attr:`~contextweaver.config.ContextPolicy.redaction_hooks`, and it strictly
tightens the sensitivity model — it never relaxes any default.
"""

from __future__ import annotations

from dataclasses import replace

from contextweaver.context.sensitivity import register_redaction_hook
from contextweaver.protocols import TokenEstimator
from contextweaver.secrets import DEFAULT_SECRET_MASK, scrub_secrets
from contextweaver.tokens import heuristic_counter
from contextweaver.types import ContextItem


class SecretRedactor:
    """Scrub well-known secret shapes from a :class:`ContextItem`'s text.

    Args:
        mask: Replacement string substituted for each detected secret.  Defaults
            to :data:`~contextweaver.secrets.DEFAULT_SECRET_MASK`.
        estimator: Token estimator used to re-size the scrubbed text so the
            number feeding budget enforcement comes from one source of truth
            (issue #530).  ``None`` uses the canonical script-aware heuristic.
    """

    def __init__(
        self, mask: str = DEFAULT_SECRET_MASK, estimator: TokenEstimator | None = None
    ) -> None:
        self._mask = mask
        self._estimator: TokenEstimator = estimator or heuristic_counter()

    def redact(self, item: ContextItem) -> ContextItem:
        """Return a copy of *item* with secret-shaped substrings masked.

        When no secret is detected the text is unchanged and the item is returned
        with its token estimate recomputed from the same source of truth.
        """
        scrubbed = scrub_secrets(item.text, mask=self._mask)
        if scrubbed == item.text:
            return item
        return replace(
            item,
            text=scrubbed,
            token_estimate=self._estimator.estimate(scrubbed),
        )


# Register the built-in hook so it can be referenced by name ("secret") in
# ContextPolicy.redaction_hooks, alongside the built-in "mask" hook.
register_redaction_hook("secret", SecretRedactor())


__all__ = ["SecretRedactor"]

"""Built-in token counting for contextweaver (issue #405).

Token counting is core to a *context* library, yet callers previously had to
bring and wire their own ``tiktoken`` to measure before/after savings — a
footgun (a missing import surfaced only when the firewall fired, then got
swallowed by a broad ``except``).  This module owns the dependency: it exposes
a first-class counter so callers never import ``tiktoken`` directly, and the
firewall/``BuildStats`` numbers are computed through the *same* counter so
reported savings match what callers measure.

``tiktoken`` is already a **core** runtime dependency (see ``pyproject.toml``),
so :func:`count` resolves an exact tokenizer by default and never hard-fails on
import.  In offline / air-gapped environments :class:`TiktokenEstimator`
transparently degrades to the character heuristic, so token counting always
returns a value.  The no-op ``contextweaver[tokenizers]`` extra documents this
contract for callers who prefer to pin the tokenizer explicitly.

Example::

    from contextweaver import tokens

    tokens.count("hello world")                # exact (cl100k_base) when available
    tokens.count("¡hola!", model="gpt-4o")     # model-specific tokenizer
    counter = tokens.get_token_counter()       # reuse a cached TokenEstimator
"""

from __future__ import annotations

from functools import lru_cache

from contextweaver.protocols import (
    CharDivFourEstimator,
    TiktokenEstimator,
    TokenEstimator,
)

#: Public alias — a token counter is exactly a
#: :class:`~contextweaver.protocols.TokenEstimator` (``estimate(text) -> int``).
#: Exposed under the friendlier "counter" name so callers reaching for token
#: counting do not have to discover the "estimator" vocabulary first.
TokenCounter = TokenEstimator

#: Default tiktoken encoding used when no model is specified.  ``cl100k_base``
#: backs GPT-3.5/4-class models and is a sensible cross-model default; pass an
#: explicit ``model`` to :func:`count` / :func:`get_token_counter` for a
#: model-specific tokenizer.
DEFAULT_ENCODING: str = "cl100k_base"


@lru_cache(maxsize=16)
def get_token_counter(model: str | None = None) -> TokenEstimator:
    """Return a cached :class:`TokenCounter` for *model*.

    Args:
        model: A model name (e.g. ``"gpt-4o"``) or a raw tiktoken encoding name
            (e.g. ``"cl100k_base"``).  ``None`` uses :data:`DEFAULT_ENCODING`.

    Returns:
        A :class:`~contextweaver.protocols.TokenEstimator`.  The result is
        memoised per *model* so repeated calls reuse the loaded encoding.  In
        offline environments the underlying :class:`TiktokenEstimator` falls
        back to the character heuristic; callers always get a usable counter.
    """
    return TiktokenEstimator(model or DEFAULT_ENCODING)


def count(text: str, *, model: str | None = None) -> int:
    """Return the token count for *text* using the built-in counter.

    Args:
        text: The text to measure.
        model: Optional model / encoding name (see :func:`get_token_counter`).
            ``None`` uses the default tiktoken encoding.

    Returns:
        The estimated token count.  Exact when the tiktoken encoding is
        available; an approximation (``len(text) // 4``) when offline.
    """
    return get_token_counter(model).estimate(text)


def heuristic_counter() -> TokenEstimator:
    """Return the dependency-free character-heuristic counter.

    Useful when a caller explicitly wants the ``len(text) // 4`` approximation
    (e.g. byte-deterministic counts that never touch the tiktoken cache) rather
    than the tokenizer-backed default.
    """
    return CharDivFourEstimator()

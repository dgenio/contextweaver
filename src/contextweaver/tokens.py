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

Provider-calibrated counting (issue #493). Integrators whose target model is
not OpenAI-family can register an accurate counter under a provider/model name
and select it by name without code changes; ``tiktoken`` stays the default::

    from contextweaver import tokens

    tokens.register_estimator("anthropic", my_anthropic_counter)
    counter = tokens.get_token_counter("anthropic")   # the registered counter
    tokens.estimator_name(counter)                    # -> "anthropic"

The estimator a build used is recorded on
:attr:`~contextweaver.envelope.BuildStats.token_estimator` so budget overshoots
can be attributed to a counter path.
"""

from __future__ import annotations

from functools import lru_cache

from contextweaver.protocols import (
    HeuristicEstimator,
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

#: Provider/model-name → counter registrations (issue #493).  Looked up *before*
#: the tiktoken default in :func:`get_token_counter`, so an integrator can make
#: an Anthropic- or Gemini-accurate counter selectable by name.  Empty by
#: default, keeping the out-of-the-box behaviour purely tiktoken-backed.
_REGISTRY: dict[str, TokenEstimator] = {}


def register_estimator(name: str, estimator: TokenEstimator) -> None:
    """Register *estimator* under *name* for provider-aware resolution (issue #493).

    Once registered, :func:`get_token_counter` returns *estimator* for that
    ``name`` (taking precedence over tiktoken encodings), and
    :func:`estimator_name` reports the registered *name* for the instance — so
    a build that uses it stamps ``BuildStats.token_estimator == name``.

    Re-registering the same *name* replaces the previous binding (last write
    wins), which keeps test setup and runtime reconfiguration simple.

    Args:
        name: Provider or model identifier (e.g. ``"anthropic"``, ``"gemini"``).
        estimator: Any :class:`~contextweaver.protocols.TokenEstimator`.
    """
    _REGISTRY[name] = estimator


def registered_estimators() -> dict[str, TokenEstimator]:
    """Return a copy of the current name → estimator registry (issue #493)."""
    return dict(_REGISTRY)


def estimator_name(counter: TokenEstimator) -> str:
    """Return a stable identifier for *counter* for observability (issue #493).

    Resolution order: a name it was :func:`register_estimator`-ed under, then
    its own ``name`` attribute (set by the built-in estimators, e.g.
    ``"tiktoken/cl100k_base"`` / ``"heuristic/v2"``), then its class name.
    """
    for name, est in _REGISTRY.items():
        if est is counter:
            return name
    own = getattr(counter, "name", None)
    return str(own) if own else type(counter).__name__


@lru_cache(maxsize=16)
def _tiktoken_counter(model: str) -> TokenEstimator:
    """Return a tiktoken-backed counter for *model*, memoised by the raw string.

    The cache key is the exact *model* string passed in (a model name like
    ``"gpt-4o"`` or an encoding name like ``"cl100k_base"``); it is **not**
    normalised to the resolved encoding, so two distinct names that happen to
    share an encoding are cached as separate instances.
    """
    return TiktokenEstimator(model)


def get_token_counter(model: str | None = None) -> TokenEstimator:
    """Return a :class:`TokenCounter` for *model*.

    Args:
        model: A registered provider/model name (see :func:`register_estimator`),
            a model name (e.g. ``"gpt-4o"``), or a raw tiktoken encoding name
            (e.g. ``"cl100k_base"``).  ``None`` uses :data:`DEFAULT_ENCODING`.

    Returns:
        A :class:`~contextweaver.protocols.TokenEstimator`.  Registered names
        resolve to their registered counter; otherwise the result is a
        tiktoken-backed counter, memoised by the raw *model* string (two model
        names that share an encoding are cached separately).  In offline
        environments the underlying :class:`TiktokenEstimator` falls back to the
        script-aware heuristic; callers always get a usable counter.
    """
    if model is not None and model in _REGISTRY:
        return _REGISTRY[model]
    return _tiktoken_counter(model or DEFAULT_ENCODING)


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
    """Return the dependency-free, script-aware heuristic counter (issue #525).

    Useful when a caller explicitly wants a byte-deterministic count that never
    touches the tiktoken cache rather than the tokenizer-backed default. The
    returned :class:`~contextweaver.protocols.HeuristicEstimator` matches
    ``len(text) // 4`` for Latin/ASCII text and counts dense scripts (CJK,
    Kana, Hangul, emoji) at ≈1 token/char so offline budgets are not
    under-counted ~4× on non-Latin content.
    """
    return HeuristicEstimator()

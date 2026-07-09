"""Private TTL and redaction-before-store helpers for
:mod:`contextweaver.store.json_file_artifacts` (issue #375).

Extracted to keep that module within the ≤300-line ceiling. Not public API.
"""

from __future__ import annotations

from collections.abc import Callable

from contextweaver.secrets import scrub_secrets

#: A monotonic clock returning seconds. ``time.monotonic`` by default.
#: Mirrors the injectable-clock convention already used by
#: :class:`~contextweaver.adapters.gateway_controls.ToolResultCache`.
Clock = Callable[[], float]


def _redact_bytes(content: bytes) -> bytes:
    """Scrub secret-shaped substrings from *content* if it decodes as UTF-8 text.

    Binary (non-UTF-8) content is returned unchanged — :func:`scrub_secrets`
    operates on decoded text, and guessing at binary structure to redact
    embedded strings would be unreliable and out of scope.
    """
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return content
    return scrub_secrets(text).encode("utf-8")


class ArtifactLifecycle:
    """Owns TTL expiry + redaction-before-store bookkeeping for one store.

    Composed into :class:`~contextweaver.store.json_file_artifacts.JsonFileArtifactStore`
    rather than inherited, so the store's own locking/quota/index logic stays
    untangled from this policy.

    Args:
        ttl_seconds: Per-artifact TTL in seconds from :meth:`record_put`.
            ``None`` never expires.
        redact_secrets: Whether :meth:`prepare` scrubs UTF-8 content before
            it is written.
        clock: Injectable monotonic clock for expiry checks.
    """

    def __init__(self, *, ttl_seconds: float | None, redact_secrets: bool, clock: Clock) -> None:
        self.ttl_seconds = ttl_seconds
        self.redact_secrets = redact_secrets
        self._clock = clock
        self._expires_at: dict[str, float] = {}

    def prepare(self, content: bytes) -> bytes:
        """Return *content*, redacted if :attr:`redact_secrets` is set."""
        return _redact_bytes(content) if self.redact_secrets else content

    def record_put(self, handle: str) -> None:
        """Stamp or clear *handle*'s expiry, called right after a successful write."""
        if self.ttl_seconds is not None:
            self._expires_at[handle] = self._clock() + self.ttl_seconds
        else:
            self._expires_at.pop(handle, None)

    def is_expired(self, handle: str) -> bool:
        """Return whether *handle*'s TTL (if any) has elapsed."""
        expires_at = self._expires_at.get(handle)
        return expires_at is not None and self._clock() >= expires_at

    def forget(self, handle: str) -> None:
        """Drop any expiry bookkeeping for *handle* (called on delete)."""
        self._expires_at.pop(handle, None)


__all__ = ["ArtifactLifecycle", "Clock"]

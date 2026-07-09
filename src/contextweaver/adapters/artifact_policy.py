"""Artifact lifecycle policy for ``mcp serve --state-dir`` (#375).

Pure-data config consumed by ``mcp serve`` to configure the persistent
:class:`~contextweaver.store.json_file_artifacts.JsonFileArtifactStore`
(TTL, quotas, and redaction-before-store).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from contextweaver.adapters._config_coerce import coerce_bool, opt_positive_number
from contextweaver.exceptions import ConfigError

_ARTIFACTS_KEYS: frozenset[str] = frozenset(
    {"ttl_seconds", "max_bytes", "max_artifacts", "redact_secrets"}
)


@dataclass(frozen=True)
class ArtifactPolicy:
    """Artifact lifecycle policy for ``mcp serve --state-dir`` (#375).

    Attributes:
        ttl_seconds: Optional per-artifact time-to-live, seconds from the
            moment it is stored. ``None`` (default) never expires. Only
            enforced by the persistent
            :class:`~contextweaver.store.json_file_artifacts.JsonFileArtifactStore`
            (the backend ``--state-dir`` uses) and is process-lifetime
            scoped: a restart resets the countdown for artifacts written in
            an earlier run (cross-restart wall-clock persistence would
            require a schema change to
            :class:`~contextweaver.types.ArtifactRef` and is tracked
            separately under issue #617).
        max_bytes: Optional ceiling on total stored artifact bytes (passed
            through to the artifact store's existing quota).
        max_artifacts: Optional ceiling on the number of stored artifacts
            (passed through to the artifact store's existing quota).
        redact_secrets: When ``True``, text artifacts are scrubbed with
            :func:`contextweaver.secrets.scrub_secrets` *before* being
            written to disk (not just before rendering into a prompt).
    """

    ttl_seconds: float | None = None
    max_bytes: int | None = None
    max_artifacts: int | None = None
    redact_secrets: bool = False

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "ttl_seconds": self.ttl_seconds,
            "max_bytes": self.max_bytes,
            "max_artifacts": self.max_artifacts,
            "redact_secrets": self.redact_secrets,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ArtifactPolicy:
        """Build from the ``artifacts`` config block."""
        if not isinstance(data, dict):
            raise ConfigError("artifacts config must be a mapping")
        unknown = sorted(set(data) - _ARTIFACTS_KEYS)
        if unknown:
            allowed = ", ".join(sorted(_ARTIFACTS_KEYS))
            raise ConfigError(f"artifacts: unknown key(s) {unknown}; allowed: {allowed}")
        return cls(
            ttl_seconds=opt_positive_number(
                "artifacts.ttl_seconds", data.get("ttl_seconds"), kind=float
            ),
            max_bytes=opt_positive_number("artifacts.max_bytes", data.get("max_bytes"), kind=int),
            max_artifacts=opt_positive_number(
                "artifacts.max_artifacts", data.get("max_artifacts"), kind=int
            ),
            redact_secrets=coerce_bool(
                "artifacts.redact_secrets", data.get("redact_secrets"), False
            ),
        )


__all__ = ["ArtifactPolicy"]

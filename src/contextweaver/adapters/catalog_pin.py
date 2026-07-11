"""Catalog pinning — detect and gate tool-surface drift (issue #656).

Operators pin the expected catalog hash (the deterministic, order-invariant
SHA-256 from :func:`contextweaver.routing.manifest.compute_catalog_hash`,
issue #48) and check the live catalog against it at startup or refresh:

* ``warn`` mode (default) reports a mismatch and continues — the caller logs
  :attr:`PinCheck.message`.
* ``strict`` mode refuses to proceed on mismatch via :func:`enforce_pin`.

Pinning catches upstream tool-surface drift — a renamed tool, an edited
description, a namespace change — before it silently alters routing.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Literal, cast

from contextweaver.exceptions import ConfigError
from contextweaver.routing.manifest import compute_catalog_hash

if TYPE_CHECKING:
    from contextweaver.types import SelectableItem

#: Supported pin enforcement modes.
PIN_MODES: frozenset[str] = frozenset({"warn", "strict"})

_PIN_KEYS: frozenset[str] = frozenset({"expected_hash", "mode"})

_REMEDIATION = "re-pin with the new hash or investigate tool-surface drift"


@dataclass(frozen=True)
class PinPolicy:
    """Operator-declared catalog pin (issue #656).

    Attributes:
        expected_hash: The pinned catalog hash, as produced by
            :func:`contextweaver.routing.manifest.compute_catalog_hash`.
        mode: ``"warn"`` (default) reports mismatches and continues;
            ``"strict"`` makes :func:`enforce_pin` raise on mismatch.
    """

    expected_hash: str
    mode: Literal["warn", "strict"] = "warn"

    def __post_init__(self) -> None:
        if not isinstance(self.expected_hash, str) or not self.expected_hash.strip():
            raise ConfigError("pin.expected_hash must be a non-empty string")
        if self.mode not in PIN_MODES:
            allowed = ", ".join(sorted(PIN_MODES))
            raise ConfigError(f"pin.mode must be one of {allowed}, got {self.mode!r}")

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {"expected_hash": self.expected_hash, "mode": self.mode}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> PinPolicy:
        """Build from a ``pin`` config block.

        Args:
            data: Mapping with ``expected_hash`` (required) and optional
                ``mode``.

        Returns:
            A validated :class:`PinPolicy`.

        Raises:
            ConfigError: If *data* is not a mapping, carries unknown keys,
                or fails field validation.
        """
        if not isinstance(data, dict):
            raise ConfigError("pin config must be a mapping")
        unknown = sorted(set(data) - _PIN_KEYS)
        if unknown:
            allowed = ", ".join(sorted(_PIN_KEYS))
            raise ConfigError(f"pin: unknown key(s) {unknown}; allowed: {allowed}")
        if "expected_hash" not in data:
            raise ConfigError("pin.expected_hash is required")
        mode = str(data.get("mode", "warn"))
        if mode not in PIN_MODES:
            allowed = ", ".join(sorted(PIN_MODES))
            raise ConfigError(f"pin.mode must be one of {allowed}, got {mode!r}")
        expected = data["expected_hash"]
        if not isinstance(expected, str):
            raise ConfigError("pin.expected_hash must be a non-empty string")
        return cls(expected_hash=expected, mode=cast('Literal["warn", "strict"]', mode))


@dataclass(frozen=True)
class PinCheck:
    """Outcome of one catalog pin check.

    Attributes:
        matched: Whether the live catalog hash equals the pinned hash.
        expected_hash: The pinned hash from the :class:`PinPolicy`.
        actual_hash: The hash computed from the live catalog.
        mode: The policy's enforcement mode at check time.
    """

    matched: bool
    expected_hash: str
    actual_hash: str
    mode: str

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "matched": self.matched,
            "expected_hash": self.expected_hash,
            "actual_hash": self.actual_hash,
            "mode": self.mode,
        }

    @property
    def message(self) -> str:
        """One-line operator-facing summary of this check."""
        if self.matched:
            return f"catalog pin ok: hash {self.actual_hash} matches pin ({self.mode} mode)"
        return (
            f"catalog pin mismatch ({self.mode} mode): expected {self.expected_hash}, "
            f"got {self.actual_hash} — {_REMEDIATION}"
        )


def check_catalog_pin(policy: PinPolicy, items: list[SelectableItem]) -> PinCheck:
    """Check the live catalog *items* against a :class:`PinPolicy`.

    The comparison uses
    :func:`contextweaver.routing.manifest.compute_catalog_hash`, which is
    invariant under item reordering and reflects id, name, description,
    namespace, and tags (metadata/examples edits do not change it).

    Args:
        policy: The operator's pin.
        items: The live catalog items.

    Returns:
        A :class:`PinCheck` describing the outcome; never raises on
        mismatch (that is :func:`enforce_pin`'s job).
    """
    actual = compute_catalog_hash(items)
    return PinCheck(
        matched=actual == policy.expected_hash,
        expected_hash=policy.expected_hash,
        actual_hash=actual,
        mode=policy.mode,
    )


def enforce_pin(check: PinCheck) -> None:
    """Apply a :class:`PinCheck` according to its mode.

    A match, or a mismatch in ``warn`` mode, is a no-op — the caller is
    expected to log :attr:`PinCheck.message`. A mismatch in ``strict`` mode
    refuses startup.

    Args:
        check: The check to enforce.

    Raises:
        ConfigError: On a mismatch in ``strict`` mode; the message carries
            both hashes and a remediation hint.
    """
    if check.matched or check.mode != "strict":
        return
    raise ConfigError(
        f"catalog pin mismatch in strict mode: expected {check.expected_hash}, "
        f"got {check.actual_hash}",
        hint=_REMEDIATION,
    )


__all__ = [
    "PIN_MODES",
    "PinCheck",
    "PinPolicy",
    "check_catalog_pin",
    "enforce_pin",
]

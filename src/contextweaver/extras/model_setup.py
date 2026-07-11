"""Local embedding-model setup helpers behind the ``models`` CLI (issue #386).

Backs ``contextweaver models doctor`` and ``contextweaver models download``:
small, optional, local-first diagnostics for the ``[embeddings]`` extra so
semantic routing (issues #8/#387) never requires hand-configuring an ML
stack.  Pure library module — the Typer wiring lives in ``__main__.py``.

Config shape (the ``models.embeddings`` block)::

    models:
      embeddings:
        provider: sentence-transformers
        model: sentence-transformers/all-MiniLM-L6-v2
        device: auto
        cache_dir: .weaver/models

Everything here is diagnostic or explicitly requested (``download``); no
model is ever fetched implicitly, matching the deterministic-first rubric's
"no huge model download by default".
"""

from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from contextweaver.exceptions import ConfigError

#: The CPU-friendly default model recommended by ``extras/embeddings.py``.
DEFAULT_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

#: Providers the config block accepts today.
PROVIDERS: tuple[str, ...] = ("sentence-transformers", "hashing")


@dataclass
class EmbeddingModelConfig:
    """Parsed ``models.embeddings`` config block."""

    provider: str = "sentence-transformers"
    model: str = DEFAULT_MODEL
    device: str = "auto"
    cache_dir: str | None = None
    batch_size: int = 32

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "provider": self.provider,
            "model": self.model,
            "device": self.device,
            "cache_dir": self.cache_dir,
            "batch_size": self.batch_size,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> EmbeddingModelConfig:
        """Build from a config mapping, validating field values."""
        if not isinstance(data, dict):
            raise ConfigError("models.embeddings must be a mapping")
        provider = str(data.get("provider", "sentence-transformers"))
        if provider not in PROVIDERS:
            raise ConfigError(f"models.embeddings.provider must be one of {PROVIDERS}")
        batch_size = data.get("batch_size", 32)
        if isinstance(batch_size, bool) or not isinstance(batch_size, int) or batch_size < 1:
            raise ConfigError("models.embeddings.batch_size must be a positive integer")
        cache_dir = data.get("cache_dir")
        return cls(
            provider=provider,
            model=str(data.get("model", DEFAULT_MODEL)),
            device=str(data.get("device", "auto")),
            cache_dir=str(cache_dir) if cache_dir is not None else None,
            batch_size=batch_size,
        )


@dataclass
class ModelCheck:
    """One doctor finding."""

    check: str
    level: str  # "ok" | "warn" | "fail"
    message: str
    hint: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {
            "check": self.check,
            "level": self.level,
            "message": self.message,
            "hint": self.hint,
        }


@dataclass
class ModelDoctorReport:
    """All findings from one ``models doctor`` run."""

    checks: list[ModelCheck] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        """``True`` when no check failed (warnings allowed)."""
        return all(check.level != "fail" for check in self.checks)

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-compatible dict."""
        return {"ok": self.ok, "checks": [check.to_dict() for check in self.checks]}

    def render_text(self) -> str:
        """One line per check with ✓ / ! / ✗ prefixes."""
        prefix = {"ok": "✓", "warn": "!", "fail": "✗"}
        lines = []
        for check in self.checks:
            line = f"{prefix.get(check.level, '?')} {check.check}: {check.message}"
            if check.hint and check.level != "ok":
                line += f" (hint: {check.hint})"
            lines.append(line)
        return "\n".join(lines)


def _cache_path(config: EmbeddingModelConfig) -> Path | None:
    """Resolve the model cache directory, when configured or inherited."""
    if config.cache_dir:
        return Path(config.cache_dir)
    env = os.environ.get("SENTENCE_TRANSFORMERS_HOME") or os.environ.get("HF_HOME")
    return Path(env) if env else None


def run_model_doctor(config: EmbeddingModelConfig | None = None) -> ModelDoctorReport:
    """Diagnose the local embedding setup without downloading anything.

    Checks: extra installed, torch importable, configured cache directory
    writability, whether the configured model already exists in the cache,
    and (always ok) the hashing fallback.  Never touches the network.
    """
    config = config or EmbeddingModelConfig()
    report = ModelDoctorReport()
    install_hint = "pip install 'contextweaver[embeddings]'"
    if config.provider == "hashing":
        report.checks.append(
            ModelCheck("provider", "ok", "hashing backend is stdlib-only; nothing to set up")
        )
        return report
    if importlib.util.find_spec("sentence_transformers") is None:
        report.checks.append(
            ModelCheck("extra", "fail", "sentence-transformers is not installed", install_hint)
        )
    else:
        report.checks.append(ModelCheck("extra", "ok", "sentence-transformers importable"))
        if importlib.util.find_spec("torch") is None:
            report.checks.append(
                ModelCheck("torch", "fail", "torch is not importable", install_hint)
            )
        else:
            report.checks.append(ModelCheck("torch", "ok", "torch importable"))
    cache = _cache_path(config)
    if cache is None:
        report.checks.append(
            ModelCheck(
                "cache",
                "warn",
                "no cache_dir configured; the provider default (~/.cache) will be used",
                "set models.embeddings.cache_dir for reproducible deployments",
            )
        )
    elif cache.exists() and os.access(cache, os.W_OK):
        report.checks.append(ModelCheck("cache", "ok", f"cache dir {cache} is writable"))
        marker = config.model.replace("/", "_")
        cached = any(marker in entry.name for entry in cache.iterdir()) if cache.is_dir() else False
        report.checks.append(
            ModelCheck(
                "model",
                "ok" if cached else "warn",
                f"model {config.model} {'found in cache' if cached else 'not cached yet'}",
                None if cached else f"run: contextweaver models download {config.model}",
            )
        )
    else:
        report.checks.append(
            ModelCheck(
                "cache",
                "fail",
                f"cache dir {cache} missing or unwritable",
                "create it or fix permissions",
            )
        )
    report.checks.append(
        ModelCheck("fallback", "ok", "HashingEmbeddingBackend available (deterministic, no extra)")
    )
    return report


def download_model(model: str = DEFAULT_MODEL, *, cache_dir: str | None = None) -> str:
    """Explicitly download *model* into the local cache and return its path.

    The only network-touching helper in this module — it exists so the
    download is a deliberate operator action, never a side effect.

    Raises:
        ConfigError: When the ``[embeddings]`` extra is missing or the
            download fails (with the underlying reason in the message).
    """
    if importlib.util.find_spec("sentence_transformers") is None:
        raise ConfigError(
            "downloading models requires the [embeddings] extra",
            hint="pip install 'contextweaver[embeddings]'",
        )
    if cache_dir:
        os.environ.setdefault("SENTENCE_TRANSFORMERS_HOME", cache_dir)
    try:
        from sentence_transformers import SentenceTransformer

        loaded = SentenceTransformer(model, cache_folder=cache_dir)
    except Exception as exc:
        raise ConfigError(
            f"failed to download/load {model!r}: {exc}",
            hint="check the model id and network access",
        ) from exc
    del loaded
    cache = cache_dir or _cache_path(EmbeddingModelConfig(model=model)) or "~/.cache"
    return f"{model} cached under {cache}"


__all__ = [
    "DEFAULT_MODEL",
    "PROVIDERS",
    "EmbeddingModelConfig",
    "ModelCheck",
    "ModelDoctorReport",
    "download_model",
    "run_model_doctor",
]

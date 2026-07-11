"""Tests for the models doctor/download helpers (issue #386)."""

from __future__ import annotations

from pathlib import Path

import pytest

from contextweaver.exceptions import ConfigError
from contextweaver.extras.model_setup import (
    DEFAULT_MODEL,
    EmbeddingModelConfig,
    download_model,
    run_model_doctor,
)


def test_config_defaults_and_serde() -> None:
    config = EmbeddingModelConfig()
    assert config.model == DEFAULT_MODEL
    assert EmbeddingModelConfig.from_dict(config.to_dict()) == config


def test_config_validation() -> None:
    with pytest.raises(ConfigError):
        EmbeddingModelConfig.from_dict({"provider": "openai"})
    with pytest.raises(ConfigError):
        EmbeddingModelConfig.from_dict({"batch_size": 0})
    with pytest.raises(ConfigError):
        EmbeddingModelConfig.from_dict("not-a-mapping")  # type: ignore[arg-type]


def test_hashing_provider_needs_no_setup() -> None:
    report = run_model_doctor(EmbeddingModelConfig(provider="hashing"))
    assert report.ok
    assert report.checks[0].message.startswith("hashing backend")


def test_doctor_without_extra_fails_with_install_hint() -> None:
    # sentence-transformers is not installed in the dev venv.
    report = run_model_doctor(EmbeddingModelConfig())
    extra = next(check for check in report.checks if check.check == "extra")
    if extra.level == "fail":
        assert "contextweaver[embeddings]" in (extra.hint or "")
        assert not report.ok
    else:  # environment happens to have the extra — doctor should be ok/warn only
        assert report.ok


def test_doctor_cache_checks(tmp_path: Path) -> None:
    config = EmbeddingModelConfig(cache_dir=str(tmp_path))
    report = run_model_doctor(config)
    cache = next(check for check in report.checks if check.check == "cache")
    assert cache.level == "ok"
    model = next(check for check in report.checks if check.check == "model")
    assert model.level == "warn"  # not cached yet
    assert "models download" in (model.hint or "")

    (tmp_path / f"models--{DEFAULT_MODEL.replace('/', '_')}").mkdir()
    recheck = run_model_doctor(config)
    model = next(check for check in recheck.checks if check.check == "model")
    assert model.level == "ok"


def test_doctor_missing_cache_dir_fails(tmp_path: Path) -> None:
    config = EmbeddingModelConfig(cache_dir=str(tmp_path / "absent"))
    report = run_model_doctor(config)
    cache = next(check for check in report.checks if check.check == "cache")
    assert cache.level == "fail"
    assert not report.ok


def test_render_text_has_prefixes(tmp_path: Path) -> None:
    text = run_model_doctor(EmbeddingModelConfig(cache_dir=str(tmp_path))).render_text()
    assert any(line.startswith(("✓", "!", "✗")) for line in text.splitlines())


def test_download_without_extra_raises() -> None:
    import importlib.util

    if importlib.util.find_spec("sentence_transformers") is not None:
        pytest.skip("embeddings extra installed; download would hit the network")
    with pytest.raises(ConfigError, match="embeddings"):
        download_model()

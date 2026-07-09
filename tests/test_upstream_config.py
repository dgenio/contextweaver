"""Tests for contextweaver.adapters.upstream_config (issues #366/#368)."""

from __future__ import annotations

import pytest

from contextweaver.adapters.upstream_config import UpstreamSpec, parse_upstreams_config
from contextweaver.exceptions import ConfigError

# ---------------------------------------------------------------------------
# UpstreamSpec construction
# ---------------------------------------------------------------------------


def test_stdio_spec_requires_command() -> None:
    with pytest.raises(ConfigError, match="requires 'command'"):
        UpstreamSpec(name="fs", type="stdio")


def test_http_spec_requires_url() -> None:
    with pytest.raises(ConfigError, match="requires 'url'"):
        UpstreamSpec(name="gh", type="http")


def test_unknown_type_rejected() -> None:
    with pytest.raises(ConfigError, match="type must be one of"):
        UpstreamSpec(name="x", type="websocket", url="http://x")


def test_empty_name_rejected() -> None:
    with pytest.raises(ConfigError, match="non-empty"):
        UpstreamSpec(name="", type="stdio", command="echo")


def test_non_positive_timeout_rejected() -> None:
    with pytest.raises(ConfigError, match="timeout must be positive"):
        UpstreamSpec(name="fs", type="stdio", command="echo", timeout=0)


# ---------------------------------------------------------------------------
# matches_tool (#368 include/exclude filters)
# ---------------------------------------------------------------------------


def test_matches_tool_no_filters_admits_everything() -> None:
    spec = UpstreamSpec(name="fs", type="stdio", command="echo")
    assert spec.matches_tool("anything") is True


def test_matches_tool_include_glob() -> None:
    spec = UpstreamSpec(name="fs", type="stdio", command="echo", include_tools=("read_*",))
    assert spec.matches_tool("read_file") is True
    assert spec.matches_tool("delete_file") is False


def test_matches_tool_exclude_wins_over_include() -> None:
    spec = UpstreamSpec(
        name="fs",
        type="stdio",
        command="echo",
        include_tools=("*",),
        exclude_tools=("delete_*",),
    )
    assert spec.matches_tool("read_file") is True
    assert spec.matches_tool("delete_file") is False


# ---------------------------------------------------------------------------
# from_dict / env interpolation
# ---------------------------------------------------------------------------


def test_from_dict_builds_stdio_spec() -> None:
    spec = UpstreamSpec.from_dict(
        "fs",
        {
            "type": "stdio",
            "command": "npx",
            "args": ["-y", "server"],
            "namespace": "fs",
            "include_tools": ["read_*"],
        },
    )
    assert spec.type == "stdio"
    assert spec.command == "npx"
    assert spec.args == ("-y", "server")
    assert spec.namespace == "fs"
    assert spec.include_tools == ("read_*",)
    assert spec.required is True


def test_from_dict_rejects_unknown_key() -> None:
    with pytest.raises(ConfigError, match="unknown key"):
        UpstreamSpec.from_dict("fs", {"type": "stdio", "command": "echo", "bogus": 1})


def test_from_dict_rejects_non_mapping() -> None:
    with pytest.raises(ConfigError, match="must be a mapping"):
        UpstreamSpec.from_dict("fs", "not-a-dict")  # type: ignore[arg-type]


def test_from_dict_interpolates_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MY_TOKEN", "secret123")
    spec = UpstreamSpec.from_dict(
        "gh",
        {
            "type": "http",
            "url": "https://example.com",
            "headers": {"Authorization": "Bearer ${env:MY_TOKEN}"},
        },
    )
    assert spec.headers["Authorization"] == "Bearer secret123"


def test_from_dict_unset_env_var_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("MISSING_VAR", raising=False)
    with pytest.raises(ConfigError, match="unset environment variable"):
        UpstreamSpec.from_dict(
            "gh",
            {"type": "http", "url": "https://example.com/${env:MISSING_VAR}"},
        )


def test_to_dict_round_trips_shape() -> None:
    spec = UpstreamSpec(name="fs", type="stdio", command="npx", args=("-y",))
    data = spec.to_dict()
    assert data["type"] == "stdio"
    assert data["command"] == "npx"
    assert data["args"] == ["-y"]


# ---------------------------------------------------------------------------
# parse_upstreams_config
# ---------------------------------------------------------------------------


def test_parse_upstreams_config_preserves_order() -> None:
    specs = parse_upstreams_config(
        {
            "a": {"type": "stdio", "command": "echo"},
            "b": {"type": "stdio", "command": "echo"},
        }
    )
    assert [s.name for s in specs] == ["a", "b"]


def test_parse_upstreams_config_rejects_empty() -> None:
    with pytest.raises(ConfigError, match="at least one upstream"):
        parse_upstreams_config({})


def test_parse_upstreams_config_rejects_non_mapping() -> None:
    with pytest.raises(ConfigError, match="must be a mapping"):
        parse_upstreams_config([])  # type: ignore[arg-type]

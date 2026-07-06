"""Tests for the resource/prompt gateway runtime (#669 / #670)."""

from __future__ import annotations

import base64
from typing import Any

import pytest

from contextweaver.adapters.gateway_error import GatewayError
from contextweaver.adapters.gateway_primitives import PrimitiveGatewayRuntime, PrimitiveUpstream
from contextweaver.adapters.mcp_primitives import (
    mcp_prompt_to_selectable,
    mcp_resource_read_to_envelope,
    mcp_resource_to_selectable,
)
from contextweaver.envelope import ChoiceCard, ResultEnvelope
from contextweaver.exceptions import CatalogError

RESOURCES = [
    {"uri": "file:///docs/readme.md", "name": "README", "mimeType": "text/markdown"},
    {"uri": "file:///docs/changelog.md", "name": "Changelog", "mimeType": "text/markdown"},
    {"uri": "postgres://db/users", "name": "users table", "description": "user records"},
]
PROMPTS = [
    {
        "name": "summarize_pr",
        "description": "Summarize a pull request",
        "arguments": [{"name": "repo", "required": True}, {"name": "number", "required": True}],
    },
    {"name": "greet", "description": "Greet a user", "arguments": [{"name": "who"}]},
]


class StubPrimitiveUpstream:
    """In-process :class:`PrimitiveUpstream` for tests."""

    def __init__(self, fail: bool = False) -> None:
        self._fail = fail

    async def list_resources(self) -> list[dict[str, Any]]:
        return [dict(r) for r in RESOURCES]

    async def read_resource(self, uri: str) -> dict[str, Any]:
        if self._fail:
            raise TimeoutError("upstream down")
        return {"contents": [{"uri": uri, "mimeType": "text/plain", "text": f"body of {uri}"}]}

    async def list_prompts(self) -> list[dict[str, Any]]:
        return [dict(p) for p in PROMPTS]

    async def get_prompt(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        return {
            "description": f"prompt {name}",
            "messages": [{"role": "user", "content": {"type": "text", "text": f"hi {arguments}"}}],
        }


def _runtime(*, fail: bool = False) -> PrimitiveGatewayRuntime:
    rt = PrimitiveGatewayRuntime(StubPrimitiveUpstream(fail=fail))
    rt.register_sync(RESOURCES, PROMPTS)
    return rt


def test_stub_satisfies_protocol() -> None:
    assert isinstance(StubPrimitiveUpstream(), PrimitiveUpstream)


def test_register_counts() -> None:
    rt = PrimitiveGatewayRuntime(StubPrimitiveUpstream())
    assert rt.register_sync(RESOURCES, PROMPTS) == (3, 2)


async def test_refresh_async_path() -> None:
    rt = PrimitiveGatewayRuntime(StubPrimitiveUpstream())
    assert await rt.refresh() == (3, 2)


def test_browse_resources_returns_resource_cards() -> None:
    cards = _runtime().browse_resources(query="readme documentation")
    assert isinstance(cards, list) and cards
    assert all(isinstance(c, ChoiceCard) for c in cards)
    assert all(c.kind == "resource" for c in cards)
    assert all("::" in c.id and c.id.startswith("resource::") for c in cards)


def test_browse_prompts_returns_prompt_cards() -> None:
    cards = _runtime().browse_prompts(query="summarize pull request")
    assert isinstance(cards, list) and cards
    assert all(c.kind == "prompt" for c in cards)
    # ChoiceCard never carries the schema, only the has_schema flag.
    assert all(not hasattr(c, "args_schema") for c in cards)


def test_browse_requires_exactly_one_selector() -> None:
    err = _runtime().browse_resources(query="x", path="/y")
    assert isinstance(err, GatewayError) and err.code == "ARGS_INVALID"


def test_browse_rejects_non_integer_top_k() -> None:
    """A non-integer top_k is rejected as ARGS_INVALID, not a TypeError crash (#671 review)."""
    err = _runtime().browse_resources(query="readme", top_k="5")  # type: ignore[arg-type]
    assert isinstance(err, GatewayError) and err.code == "ARGS_INVALID"


def test_browse_rejects_non_positive_top_k() -> None:
    """A zero/negative top_k is also rejected cleanly (#671 review)."""
    err = _runtime().browse_prompts(query="summarize", top_k=0)
    assert isinstance(err, GatewayError) and err.code == "ARGS_INVALID"


def test_resource_read_base64_decodes_blob() -> None:
    """A binary `blob` part is base64-decoded back to its original bytes (#671 review)."""
    raw = b"\x89PNG\r\n\x1a\n\x00\x01\x02\x03"
    result = {
        "contents": [
            {
                "uri": "file:///x.bin",
                "mimeType": "application/octet-stream",
                "blob": base64.b64encode(raw).decode(),
            }
        ]
    }
    envelope, binaries, _ = mcp_resource_read_to_envelope(result, "resource::fs:x#deadbeef")
    stored, _mime, _label = next(iter(binaries.values()))
    # Persisted bytes are the decoded payload, not the base64 text bytes.
    assert stored == raw
    assert envelope.artifacts[0].size_bytes == len(raw)


def test_resource_read_malformed_blob_falls_back_to_raw_bytes() -> None:
    """A non-base64 blob falls back to its raw string bytes instead of raising (#671 review)."""
    result = {
        "contents": [{"uri": "file:///x", "mimeType": "application/octet-stream", "blob": "!!!"}]
    }
    _envelope, binaries, _ = mcp_resource_read_to_envelope(result, "resource::fs:x#deadbeef")
    stored, _mime, _label = next(iter(binaries.values()))
    assert stored == b"!!!"


async def test_read_resource_firewalls_and_persists() -> None:
    rt = _runtime()
    cards = rt.browse_resources(query="readme")
    assert isinstance(cards, list) and cards
    envelope = await rt.read_resource(cards[0].id)
    assert isinstance(envelope, ResultEnvelope)
    assert envelope.status == "ok"
    assert envelope.provenance["primitive"] == "resource"
    # The read content is persisted on the shared artifact store for tool_view.
    assert rt.context_manager.artifact_store.list_refs()


async def test_read_unknown_resource_is_not_found() -> None:
    err = await _runtime().read_resource("resource::fs:missing#deadbeef")
    assert isinstance(err, GatewayError) and err.code == "RESOURCE_NOT_FOUND"


async def test_read_resource_classifies_upstream_failure() -> None:
    rt = PrimitiveGatewayRuntime(StubPrimitiveUpstream(fail=True))
    rt.register_sync(RESOURCES, PROMPTS)
    cards = rt.browse_resources(query="readme")
    assert isinstance(cards, list) and cards
    err = await rt.read_resource(cards[0].id)
    assert isinstance(err, GatewayError)
    assert err.code == "UPSTREAM_TIMEOUT"
    assert err.retryable is True


async def test_get_prompt_validates_required_args() -> None:
    rt = _runtime()
    cards = rt.browse_prompts(query="summarize pull request")
    assert isinstance(cards, list)
    pid = next(c.id for c in cards if "summarize" in c.id)
    missing = await rt.get_prompt(pid, {"repo": "acme/app"})  # missing 'number'
    assert isinstance(missing, GatewayError) and missing.code == "ARGS_INVALID"
    ok = await rt.get_prompt(pid, {"repo": "acme/app", "number": "12"})
    assert isinstance(ok, ResultEnvelope)
    assert ok.provenance["primitive"] == "prompt"


async def test_get_unknown_prompt_is_not_found() -> None:
    err = await _runtime().get_prompt("prompt::gh:missing#deadbeef", {})
    assert isinstance(err, GatewayError) and err.code == "PROMPT_NOT_FOUND"


def test_empty_catalogs_browse_empty() -> None:
    rt = PrimitiveGatewayRuntime(StubPrimitiveUpstream())
    assert rt.browse_resources(query="anything") == []
    assert rt.browse_prompts(query="anything") == []


def test_malformed_defs_are_skipped() -> None:
    rt = PrimitiveGatewayRuntime(StubPrimitiveUpstream())
    n_res, n_prompt = rt.register_sync(
        [{"uri": "file:///ok"}, {"no_uri": True}], [{"name": "ok"}, {"missing": "name"}]
    )
    assert n_res == 1 and n_prompt == 1


def test_duplicate_resources_dedup_by_id() -> None:
    rt = PrimitiveGatewayRuntime(StubPrimitiveUpstream())
    dup = [{"uri": "file:///same.md", "name": "a"}, {"uri": "file:///same.md", "name": "a"}]
    n_res, _ = rt.register_sync(dup, [])
    assert n_res == 1


# --- converter edge cases ---------------------------------------------------


def test_resource_converter_requires_uri() -> None:
    with pytest.raises(CatalogError, match="uri"):
        mcp_resource_to_selectable({"name": "x"})


def test_prompt_converter_requires_name() -> None:
    with pytest.raises(CatalogError, match="name"):
        mcp_prompt_to_selectable({"description": "x"})


def test_prompt_args_schema_marks_required() -> None:
    item = mcp_prompt_to_selectable(PROMPTS[0])
    assert item.args_schema["required"] == ["number", "repo"]
    assert item.kind == "prompt"


# --- secret-scrub parity with the tool path (issue #743) --------------------

# Assembled from fragments so the literal never appears verbatim in source
# (secret-scanner push protection), matching tests/test_secrets.py.
_SECRET = "sk-ant-" + "api03-" + "zY9xW8vU" * 3
_SECRET_MASK = "[REDACTED-SECRET]"
_SECRET_RESOURCES = [
    {"uri": "file:///creds.txt", "name": "creds", "description": f"leaked {_SECRET} here"}
]
_SECRET_PROMPTS = [{"name": "creds", "description": f"leaked {_SECRET} here"}]


def test_resource_cards_scrubbed_when_redact_enabled() -> None:
    rt = PrimitiveGatewayRuntime(StubPrimitiveUpstream(), redact_secrets=True)
    rt.register_sync(_SECRET_RESOURCES, _SECRET_PROMPTS)
    cards = rt.browse_resources(query="creds")
    assert isinstance(cards, list) and cards, "expected a routed card"
    joined = " ".join(c.description for c in cards)
    assert _SECRET not in joined
    assert _SECRET_MASK in joined


def test_prompt_cards_scrubbed_when_redact_enabled() -> None:
    rt = PrimitiveGatewayRuntime(StubPrimitiveUpstream(), redact_secrets=True)
    rt.register_sync(_SECRET_RESOURCES, _SECRET_PROMPTS)
    cards = rt.browse_prompts(query="creds")
    assert isinstance(cards, list) and cards
    joined = " ".join(c.description for c in cards)
    assert _SECRET not in joined
    assert _SECRET_MASK in joined


def test_primitive_cards_not_scrubbed_by_default() -> None:
    # Default posture is off (owned by #744); the flag must actually gate the
    # scrub — this is the regression the shared helper (#743) guards.
    #
    # Card rendering also applies an unrelated §2.3 token budget
    # (routing/cards._enforce_card_budget) that can truncate the tail of a
    # long description regardless of redaction, and the exact cut point
    # depends on the active tokenizer (real tiktoken in CI vs. the offline
    # heuristic fallback locally). So this checks a truncation-safe leading
    # fragment of the secret rather than the full string, plus the mask's
    # absence — sufficient to prove redaction did not fire.
    rt = PrimitiveGatewayRuntime(StubPrimitiveUpstream())
    rt.register_sync(_SECRET_RESOURCES, _SECRET_PROMPTS)
    cards = rt.browse_resources(query="creds")
    assert isinstance(cards, list) and cards
    joined = " ".join(c.description for c in cards)
    assert _SECRET[:12] in joined
    assert _SECRET_MASK not in joined


def test_browse_surface_name_in_args_invalid_message() -> None:
    # The shared helper reports the primitive surface (not "tool_browse").
    rt = _runtime()
    res_err = rt.browse_resources(query="x", path="y")
    prm_err = rt.browse_prompts(query="x", path="y")
    assert isinstance(res_err, GatewayError) and "resource_browse" in res_err.message
    assert isinstance(prm_err, GatewayError) and "prompt_browse" in prm_err.message

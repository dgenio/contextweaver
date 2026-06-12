"""Redis-backed store backends (issue #426).

Run the conformance kit against ``RedisArtifactStore`` / ``RedisEventLog`` using
``fakeredis`` (an in-process Redis fake), plus TTL, namespace-isolation, and
missing-config behaviour.  Skips cleanly if ``fakeredis`` is unavailable.
"""

from __future__ import annotations

import pytest

from contextweaver.exceptions import ConfigError
from contextweaver.store.redis_artifacts import RedisArtifactStore
from contextweaver.store.redis_event_log import RedisEventLog
from contextweaver.store.testing import (
    check_artifact_store_conformance,
    check_event_log_conformance,
)
from contextweaver.types import ContextItem, ItemKind

try:
    import fakeredis
except ImportError:  # pragma: no cover - fakeredis is a dev dependency
    fakeredis = None  # type: ignore[assignment]

pytestmark = pytest.mark.skipif(fakeredis is None, reason="fakeredis not installed")


def _client() -> object:
    return fakeredis.FakeStrictRedis()


def test_redis_artifact_store_conformance() -> None:
    check_artifact_store_conformance(lambda: RedisArtifactStore(client=_client()))


def test_redis_event_log_conformance() -> None:
    check_event_log_conformance(lambda: RedisEventLog(client=_client()))


def test_redis_artifact_namespaces_are_isolated() -> None:
    shared = _client()
    a = RedisArtifactStore(client=shared, namespace="tenant-a")
    b = RedisArtifactStore(client=shared, namespace="tenant-b")
    a.put("h1", b"alpha")
    assert a.exists("h1") is True
    assert b.exists("h1") is False
    assert [r.handle for r in b.list_refs()] == []


def test_redis_artifact_ttl_sets_expiry() -> None:
    client = _client()
    store = RedisArtifactStore(client=client, ttl_seconds=120)
    store.put("h1", b"data")
    # The data key must carry a positive TTL (<= the configured ceiling).
    ttl = client.ttl("cw:art:data:h1")
    assert 0 < ttl <= 120


def test_redis_event_log_preserves_order_and_children() -> None:
    log = RedisEventLog(client=_client())
    log.append(ContextItem(id="a", kind=ItemKind.user_turn, text="1"))
    log.append(ContextItem(id="b", kind=ItemKind.tool_call, text="2", parent_id="a"))
    assert [i.id for i in log.all()] == ["a", "b"]
    assert [i.id for i in log.children("a")] == ["b"]


def test_missing_client_and_url_raises_config_error() -> None:
    with pytest.raises(ConfigError):
        RedisArtifactStore()
    with pytest.raises(ConfigError):
        RedisEventLog()

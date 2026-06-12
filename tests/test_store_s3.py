"""S3-compatible artifact store (issue #426).

Run the conformance kit against ``S3ArtifactStore`` using ``moto`` to mock the
S3 API in-process, plus prefix-isolation and missing-config behaviour.  Skips
cleanly if ``moto``/``boto3`` are unavailable.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterator

import pytest

from contextweaver.exceptions import ConfigError
from contextweaver.store.s3_artifacts import S3ArtifactStore
from contextweaver.store.testing import check_artifact_store_conformance

try:
    import boto3
    import moto
except ImportError:  # pragma: no cover - moto/boto3 are dev dependencies
    boto3 = None  # type: ignore[assignment]
    moto = None  # type: ignore[assignment]

pytestmark = pytest.mark.skipif(
    boto3 is None or moto is None, reason="moto/boto3 not installed"
)

_BUCKET = "cw-artifacts"


@pytest.fixture
def s3_client() -> Iterator[object]:
    with moto.mock_aws():
        client = boto3.client("s3", region_name="us-east-1")
        client.create_bucket(Bucket=_BUCKET)
        yield client


def test_s3_artifact_store_conformance(s3_client: object) -> None:
    store = S3ArtifactStore(_BUCKET, client=s3_client)
    check_artifact_store_conformance(lambda: store)


def test_s3_prefixes_are_isolated(s3_client: object) -> None:
    a = S3ArtifactStore(_BUCKET, client=s3_client, prefix="tenant-a")
    b = S3ArtifactStore(_BUCKET, client=s3_client, prefix="tenant-b")
    a.put("h1", b"alpha", media_type="text/plain")
    assert a.exists("h1") is True
    assert b.exists("h1") is False
    assert [r.handle for r in a.list_refs()] == ["h1"]
    assert [r.handle for r in b.list_refs()] == []


def test_s3_drilldown_and_content_hash(s3_client: object) -> None:
    store = S3ArtifactStore(_BUCKET, client=s3_client)
    ref = store.put("doc", b"line one\nline two\nline three", media_type="text/plain")
    assert ref.content_hash == hashlib.sha256(b"line one\nline two\nline three").hexdigest()
    assert store.drilldown("doc", {"type": "lines", "start": 0, "end": 1}) == "line one"


def test_empty_bucket_name_raises_config_error() -> None:
    with pytest.raises(ConfigError):
        S3ArtifactStore("")

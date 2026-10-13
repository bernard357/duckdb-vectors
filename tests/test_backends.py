"""Real embedding backends: Bedrock (stubbed) and local (skipped if absent).

No live network calls: the Bedrock client is a stub, and the local backend is
skipped unless fastembed is installed.
"""

import importlib.util
import json

import pytest

from duckdb_vectors.embedders.bedrock import BedrockEmbedder
from duckdb_vectors.embedders.registry import resolve_embedder

_HAS_FASTEMBED = importlib.util.find_spec("fastembed") is not None


class _StubBody:
    def __init__(self, vector):
        self._payload = json.dumps({"embedding": vector}).encode("utf-8")

    def read(self):
        return self._payload


class _StubClient:
    def __init__(self, vector):
        self._vector = vector
        self.calls = 0

    def invoke_model(self, modelId, body):  # noqa: N803 - boto3 kwarg name
        self.calls += 1
        return {"body": _StubBody(self._vector)}


class _StubSession:
    def __init__(self, client):
        self._client = client

    def client(self, service, region_name=None):
        return self._client


def test_bedrock_embeds_via_injected_session():
    stub = _StubClient([0.1, 0.2, 0.3, 0.4])
    # An unrecognized model id has no pre-known dimension, so the embedder
    # learns it from the returned vector rather than a lookup table.
    embedder = BedrockEmbedder(model="custom.embed-model", session=_StubSession(stub))
    vectors = embedder.embed(["one", "two"])
    assert len(vectors) == 2
    assert vectors[0] == [0.1, 0.2, 0.3, 0.4]
    assert embedder.dimensions == 4
    assert stub.calls == 2


def test_bedrock_known_model_reports_catalog_dimension():
    embedder = BedrockEmbedder(model="titan")
    assert embedder.dimensions == 1024


def test_bedrock_model_identifier_folds_model_id():
    embedder = BedrockEmbedder(model="titan")
    assert embedder.model_identifier == "bedrock:amazon.titan-embed-text-v2:0"


def test_registry_resolves_bedrock():
    embedder = resolve_embedder("bedrock:titan")
    assert isinstance(embedder, BedrockEmbedder)


@pytest.mark.skipif(not _HAS_FASTEMBED, reason="fastembed not installed")
@pytest.mark.slow
def test_local_backend_embeds_when_installed():
    embedder = resolve_embedder("local")
    vectors = embedder.embed(["semantic search over markdown"])
    assert len(vectors[0]) == 384


def test_local_import_error_is_actionable_when_missing():
    if _HAS_FASTEMBED:
        pytest.skip("fastembed installed; cannot test missing-extra path")
    from duckdb_vectors.embedders.local import LocalEmbedder

    with pytest.raises(ImportError, match="duckdb-vectors\\[local\\]"):
        LocalEmbedder().embed(["x"])

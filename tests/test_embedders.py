"""Embedder registry, the hashing backend, and the fastembed/hf loaders."""

import numpy as np
import pytest

from duckdb_vectors.config import DEFAULT_BACKEND
from duckdb_vectors.embedders.fastembed import (
    _KNOWN_DIMENSIONS,
    DEFAULT_MODEL,
    FastembedEmbedder,
)
from duckdb_vectors.embedders.hashing import HashingEmbedder
from duckdb_vectors.embedders.huggingface import (
    HuggingFaceEmbedder,
    last_token_pool,
    mean_pool,
)
from duckdb_vectors.embedders.registry import (
    register_embedder,
    resolve_embedder,
)


def test_hashing_is_deterministic_and_sized():
    embedder = HashingEmbedder(dimensions=384)
    first = embedder.embed(["the quick brown fox"])
    second = embedder.embed(["the quick brown fox"])
    assert first == second
    assert len(first[0]) == 384


def test_registry_resolves_hashing():
    embedder = resolve_embedder("hashing")
    assert embedder.dimensions == 384
    assert embedder.model_identifier.startswith("hashing:")


def test_registry_resolves_variant():
    embedder = resolve_embedder("hashing:128")
    assert embedder.dimensions == 128


def test_registry_raises_on_unknown():
    with pytest.raises(ValueError, match="Unknown embedding backend"):
        resolve_embedder("does-not-exist")


def test_registry_raises_on_removed_local_backend():
    with pytest.raises(ValueError, match="Unknown embedding backend 'local'"):
        resolve_embedder("local")


def test_contributor_registration_hook():
    register_embedder("custom", lambda variant: HashingEmbedder(dimensions=8))
    embedder = resolve_embedder("custom")
    assert embedder.dimensions == 8


def test_fastembed_known_dimensions_map():
    assert _KNOWN_DIMENSIONS == {
        "minishlab/potion-multilingual-128M": 256,
        "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2": 384,
        "sentence-transformers/paraphrase-multilingual-mpnet-base-v2": 768,
        "intfloat/multilingual-e5-large": 1024,
        "BAAI/bge-small-en-v1.5": 384,
        "BAAI/bge-base-en-v1.5": 768,
        "BAAI/bge-large-en-v1.5": 1024,
    }


def test_fastembed_model_identifier_prefix():
    assert FastembedEmbedder().model_identifier == f"fastembed:{DEFAULT_MODEL}"
    assert FastembedEmbedder().model_identifier.startswith("fastembed:")


def test_default_backend_resolves_to_potion_multilingual():
    assert DEFAULT_BACKEND == "fastembed:minishlab/potion-multilingual-128M"


def test_mean_pool_weights_by_attention_mask():
    hidden = np.array([[[1.0, 1.0], [3.0, 3.0]]])
    mask = np.array([[1, 0]])
    pooled = mean_pool(hidden, mask)
    # Only the first token counts; the padded second token is masked out.
    assert pooled.tolist() == [[1.0, 1.0]]


def test_last_token_pool_selects_last_unpadded():
    hidden = np.array([[[1.0, 1.0], [2.0, 2.0], [9.0, 9.0]]])
    mask = np.array([[1, 1, 0]])
    pooled = last_token_pool(hidden, mask)
    assert pooled.tolist() == [[2.0, 2.0]]


class _StubTokenizerEncoding:
    def __init__(self, ids, attention_mask):
        self.ids = ids
        self.attention_mask = attention_mask


class _StubTokenizer:
    """A tiny tokenizer stub: each text becomes a fixed two-token sequence."""

    def encode_batch(self, texts):
        return [_StubTokenizerEncoding([1, 2], [1, 1]) for _ in texts]


class _StubInput:
    def __init__(self, name):
        self.name = name


class _StubSession:
    """Return one deterministic hidden-state row per text, hidden size 3."""

    def get_inputs(self):
        return [_StubInput("input_ids"), _StubInput("attention_mask")]

    def run(self, output_names, feeds):
        batch = feeds["input_ids"].shape[0]
        seq = feeds["input_ids"].shape[1]
        hidden = np.tile(np.array([[0.1, 0.2, 0.3]], dtype=np.float32), (batch, seq, 1))
        return [hidden]


def test_hf_embedder_with_injected_session_and_tokenizer():
    embedder = HuggingFaceEmbedder(
        repo_id="some/repo",
        session=_StubSession(),
        tokenizer=_StubTokenizer(),
    )
    assert embedder.model_identifier == "hf:some/repo"
    vectors = embedder.embed(["hello", "world"])
    assert len(vectors) == 2
    # Mean-pool of two identical [0.1, 0.2, 0.3] rows is the same vector.
    assert vectors[0] == pytest.approx([0.1, 0.2, 0.3])
    assert embedder.dimensions == 3


def test_hf_embedder_uses_last_token_pooling_for_qwen():
    embedder = HuggingFaceEmbedder(repo_id="Qwen/Qwen3-Embedding-0.6B")
    assert embedder._pooling == "last"


def test_hf_embedder_requires_repo_id():
    with pytest.raises(ValueError, match="requires a repository id"):
        HuggingFaceEmbedder(repo_id="")


def test_registry_resolves_hf_lazily():
    embedder = resolve_embedder("hf:acme/model")
    assert embedder.model_identifier == "hf:acme/model"

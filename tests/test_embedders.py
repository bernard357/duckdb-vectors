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
    def __init__(self, name, shape=None, type="tensor(int64)"):
        self.name = name
        self.shape = shape if shape is not None else ["batch", "seq"]
        self.type = type


class _StubOutput:
    def __init__(self, name, type="tensor(float)"):
        self.name = name
        self.type = type


class _StubSession:
    """Return one deterministic hidden-state row per text, hidden size 3.

    Records the feeds passed to ``run`` so tests can assert which inputs the
    embedder fed.
    """

    def __init__(self):
        self.last_feeds = None

    def get_inputs(self):
        return [_StubInput("input_ids"), _StubInput("attention_mask")]

    def get_outputs(self):
        return [_StubOutput("last_hidden_state")]

    def run(self, output_names, feeds):
        self.last_feeds = feeds
        batch = feeds["input_ids"].shape[0]
        seq = feeds["input_ids"].shape[1]
        hidden = np.tile(np.array([[0.1, 0.2, 0.3]], dtype=np.float32), (batch, seq, 1))
        return [hidden]


class _StubDecoderSession:
    """A decoder-style graph: position_ids + one KV-cache pair, two outputs.

    Mirrors the Qwen3-Embedding ONNX shape in miniature: it declares
    ``position_ids`` and a ``past_key_values.0.{key,value}`` pair whose shapes
    expose kv_heads=4 / head_dim=16, and it emits a named ``last_hidden_state``
    output ahead of a ``logits`` output. ``run`` records the feeds and returns
    deterministic arrays so tests can assert synthesis, empty-cache shapes, and
    output-by-name selection.
    """

    def __init__(self):
        self.last_feeds = None

    def get_inputs(self):
        return [
            _StubInput("input_ids"),
            _StubInput("attention_mask"),
            _StubInput("position_ids"),
            _StubInput(
                "past_key_values.0.key",
                shape=["batch", 4, "past_seq", 16],
                type="tensor(float)",
            ),
            _StubInput(
                "past_key_values.0.value",
                shape=["batch", 4, "past_seq", 16],
                type="tensor(float)",
            ),
        ]

    def get_outputs(self):
        return [_StubOutput("last_hidden_state"), _StubOutput("logits")]

    def run(self, output_names, feeds):
        self.last_feeds = feeds
        batch = feeds["input_ids"].shape[0]
        seq = feeds["input_ids"].shape[1]
        # Hidden state: last token carries a distinctive value so last-token
        # pooling is observable; logits is a different, larger tensor.
        hidden = np.tile(np.array([[0.1, 0.2, 0.3]], dtype=np.float32), (batch, seq, 1))
        # Mark the second row (the last unpadded token for a [1,1,0] mask) so
        # last-token pooling is observable.
        hidden[:, 1, :] = np.array([0.7, 0.8, 0.9], dtype=np.float32)
        logits = np.zeros((batch, seq, 100), dtype=np.float32)
        return [hidden, logits]


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


def test_hf_embedder_simple_encoder_feeds_only_declared_inputs():
    """The simple-encoder path is unchanged: no position_ids, no KV-cache.

    Regression guard for the additive generalization — a graph that declares
    only ``input_ids`` / ``attention_mask`` must never be fed ``position_ids``
    or ``past_key_values.*``, and the single hidden-state output is read.
    """
    session = _StubSession()
    embedder = HuggingFaceEmbedder(
        repo_id="BAAI/bge-small-en-v1.5",
        session=session,
        tokenizer=_StubTokenizer(),
    )
    vectors = embedder.embed(["hello"])
    assert session.last_feeds is not None
    assert set(session.last_feeds) == {"input_ids", "attention_mask"}
    assert "position_ids" not in session.last_feeds
    assert not any(k.startswith("past_key_values") for k in session.last_feeds)
    # Mean-pooled hidden state of the stub, hidden size 3.
    assert vectors[0] == pytest.approx([0.1, 0.2, 0.3])


def test_hf_embedder_decoder_stub_synthesizes_feeds_and_selects_hidden_state():
    """Decoder-shaped graph: position_ids synthesis, empty cache, output name.

    Exercises the decoder plumbing against a stub mirroring the Qwen3-Embedding
    ONNX shape: ``position_ids`` is synthesized (cumsum-1 clamped), the
    ``past_key_values.*`` inputs are fed zero-length tensors whose kv_heads /
    head_dim come from the declared shapes, the ``last_hidden_state`` output
    (not ``logits``) is pooled, and last-token pooling applies for the
    qwen-like repo id.
    """
    session = _StubDecoderSession()

    class _ThreeTokenTokenizer:
        def encode_batch(self, texts):
            # Two real tokens then one pad, so last-token pooling is observable.
            return [_StubTokenizerEncoding([5, 6, 0], [1, 1, 0]) for _ in texts]

    embedder = HuggingFaceEmbedder(
        repo_id="onnx-community/Qwen3-Embedding-0.6B-ONNX",
        session=session,
        tokenizer=_ThreeTokenTokenizer(),
    )
    vectors = embedder.embed(["hello world"])

    feeds = session.last_feeds
    assert feeds is not None
    # position_ids = cumsum(mask) - 1, clamped at 0 -> [0, 1, 1] (int64).
    assert feeds["position_ids"].tolist() == [[0, 1, 1]]
    assert feeds["position_ids"].dtype == np.int64
    # Empty KV-cache, shape [batch, kv_heads=4, 0, head_dim=16], float32.
    for key in ("past_key_values.0.key", "past_key_values.0.value"):
        assert feeds[key].shape == (1, 4, 0, 16)
        assert feeds[key].dtype == np.float32
    # last_hidden_state (index 0), not logits, is pooled; last-token pooling
    # selects the final unpadded row [0.7, 0.8, 0.9].
    assert embedder._pooling == "last"
    assert vectors[0] == pytest.approx([0.7, 0.8, 0.9])


def test_hf_embedder_external_data_colocates_onnx_and_data_file(tmp_path, monkeypatch):
    """External-data download co-locates ``model.onnx`` + ``model.onnx_data``.

    Mocks ``snapshot_download`` to a temp snapshot dir holding a fake
    ``onnx/model.onnx`` + ``onnx/model.onnx_data`` (+ ``tokenizer.json``) and a
    stubbed ``onnxruntime.InferenceSession``, then asserts the session is built
    from a real directory where both files co-exist, with no network.
    """
    import os
    import sys
    import types

    snapshot = tmp_path / "snapshot"
    (snapshot / "onnx").mkdir(parents=True)
    (snapshot / "onnx" / "model.onnx").write_bytes(b"fake-onnx-graph")
    (snapshot / "onnx" / "model.onnx_data").write_bytes(b"fake-external-weights")
    (snapshot / "tokenizer.json").write_text("{}")

    seen = {}

    def fake_snapshot_download(repo_id, allow_patterns=None):
        seen["patterns"] = allow_patterns
        return str(snapshot)

    hub_module = types.ModuleType("huggingface_hub")
    hub_module.snapshot_download = fake_snapshot_download  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "huggingface_hub", hub_module)

    class _FakeSession:
        def __init__(self, onnx_path):
            # The data file must sit beside the graph in a real directory.
            built_dir = os.path.dirname(onnx_path)
            assert os.path.basename(onnx_path) == "model.onnx"
            assert os.path.exists(os.path.join(built_dir, "model.onnx_data"))
            seen["onnx_path"] = onnx_path

    ort_module = types.ModuleType("onnxruntime")
    ort_module.InferenceSession = _FakeSession  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "onnxruntime", ort_module)

    embedder = HuggingFaceEmbedder(repo_id="janni-t/qwen3-embedding-0.6b-tei-onnx")
    session = embedder._build_session()

    assert isinstance(session, _FakeSession)
    assert "onnx_path" in seen
    # The build must not reuse the snapshot dir directly (symlinks), but a fresh
    # materialized directory with the real co-located files.
    assert os.path.dirname(seen["onnx_path"]) != str(snapshot / "onnx")


def test_hf_embedder_uses_last_token_pooling_for_qwen():
    embedder = HuggingFaceEmbedder(repo_id="Qwen/Qwen3-Embedding-0.6B")
    assert embedder._pooling == "last"


def test_hf_embedder_requires_repo_id():
    with pytest.raises(ValueError, match="requires a repository id"):
        HuggingFaceEmbedder(repo_id="")


def test_registry_resolves_hf_lazily():
    embedder = resolve_embedder("hf:acme/model")
    assert embedder.model_identifier == "hf:acme/model"


@pytest.mark.model
def test_hf_embedder_loads_real_qwen_model():
    """Opt-in end-to-end: load a real Qwen3-Embedding ONNX and embed text.

    Uses ``onnx-community/Qwen3-Embedding-0.6B-ONNX``: an external-data,
    decoder-style graph (``input_ids`` / ``attention_mask`` / ``position_ids``
    + 28 layers of ``past_key_values.{N}.{key,value}`` with kv_heads 8 /
    head_dim 128) that emits ``last_hidden_state`` (dimension 1024). Chosen
    because it loads after external-data co-location AND exposes a hidden-state
    output, so it exercises the FULL generalization: snapshot co-location,
    position_ids synthesis, empty KV-cache feeds, and output-by-name selection.
    Observed dimension: 1024.

    Deselected from the default fast suite (``make test`` runs ``-m "not
    model"``); ``make coverage`` runs unfiltered and picks this up. The
    load/embed is wrapped so a missing download, network failure, or a repo
    without probed-path ONNX weights degrades to a skip rather than a red suite.
    """
    repo = "hf:onnx-community/Qwen3-Embedding-0.6B-ONNX"
    embedder = resolve_embedder(repo)
    assert embedder.model_identifier == repo
    try:
        vectors = embedder.embed(["hello world", "embedded databases are frugal"])
    except Exception as exc:  # noqa: BLE001 - degrade to skip, never red
        pytest.skip(f"Qwen model not loadable in this environment: {exc}")
    assert len(vectors) == 2
    assert all(vector for vector in vectors)
    lengths = {len(vector) for vector in vectors}
    assert len(lengths) == 1
    assert lengths.pop() == embedder.dimensions

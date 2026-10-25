"""Core Hugging Face ONNX embedding backend (``hf:<repo-id>``).

Loads any Hugging Face repository that ships ONNX weights and a
``tokenizer.json``, runs inference through the ONNX runtime that fastembed
already vendors (no second runtime, no torch, no transformers), and mean-pools
the last hidden state. Last-token pooling is supported for Qwen3-style models.

The loader handles three ONNX shapes, additively:

- __Simple encoders__ (bge/e5/sentence-transformers): ``input_ids`` /
  ``attention_mask`` (/ ``token_type_ids``) -> ``last_hidden_state``.
- __External-data ONNX__: weights live in a sibling ``model.onnx_data``. The
  Hub cache stores both files as symlinks into a blob store, which onnxruntime
  rejects ("External data path escapes model directory"); the loader
  co-locates the real files in one directory so the data file sits beside the
  graph.
- __Decoder-style embedding graphs__ (Qwen3-Embedding and relatives): the graph
  declares ``position_ids`` and per-layer ``past_key_values.*`` cache inputs.
  The loader synthesizes ``position_ids`` from the attention mask and feeds
  zero-length cache tensors (shapes read from the graph) so one forward pass
  runs without implementing generation, then selects the hidden-state output by
  name (never ``logits``).

Everything heavy is imported lazily inside methods so the module imports
cleanly offline and is unit-testable with a stubbed session and tokenizer.
"""

from collections.abc import Sequence
from typing import Any

# Common in-repo locations for the exported ONNX graph, tried in order.
_ONNX_CANDIDATES = (
    "onnx/model.onnx",
    "model.onnx",
    "onnx/model_quantized.onnx",
    "model_quantized.onnx",
)

# Files worth materializing from the snapshot: the ONNX graph and its external
# data file live under the repo root or an ``onnx/`` subfolder, the tokenizer at
# the root.
_SNAPSHOT_PATTERNS = (
    "onnx/*",
    "*.onnx",
    "*.onnx_data",
    "tokenizer.json",
    "*.json",
)


def mean_pool(last_hidden_state, attention_mask):
    """Mean-pool token vectors weighted by the attention mask.

    ``last_hidden_state`` has shape ``(batch, seq, hidden)`` and
    ``attention_mask`` shape ``(batch, seq)``. Returns ``(batch, hidden)``.
    """
    import numpy as np

    mask = np.asarray(attention_mask, dtype=np.float32)[:, :, None]
    hidden = np.asarray(last_hidden_state, dtype=np.float32)
    summed = (hidden * mask).sum(axis=1)
    counts = np.clip(mask.sum(axis=1), a_min=1e-9, a_max=None)
    return summed / counts


def last_token_pool(last_hidden_state, attention_mask):
    """Select the last non-padding token vector per row (Qwen3-style).

    Returns ``(batch, hidden)`` taking, for each row, the hidden state at the
    final position whose attention mask is set.
    """
    import numpy as np

    hidden = np.asarray(last_hidden_state, dtype=np.float32)
    mask = np.asarray(attention_mask)
    lengths = mask.sum(axis=1).astype(int)
    indices = np.clip(lengths - 1, a_min=0, a_max=None)
    return hidden[np.arange(hidden.shape[0]), indices]


class HuggingFaceEmbedder:
    """Embed text with an ONNX model downloaded from a Hugging Face repo.

    ``hf:<repo-id>`` resolves to a repository; ONNX weights and
    ``tokenizer.json`` are downloaded via ``huggingface_hub`` on first use and
    run through ``onnxruntime``. Pooling defaults to mean-pool over the last
    hidden state; it switches to last-token pooling for repos whose id contains
    ``qwen`` (override with the ``pooling`` argument).

    Tests may inject a prebuilt ``session`` and ``tokenizer`` (or a local
    ``model_dir``) so no network or real model is needed.
    """

    def __init__(
        self,
        repo_id: str,
        pooling: str | None = None,
        session=None,
        tokenizer=None,
        model_dir: str | None = None,
        max_length: int = 512,
    ) -> None:
        if not repo_id:
            raise ValueError(
                "The 'hf' backend requires a repository id, e.g. "
                "'hf:Qwen/Qwen3-Embedding-0.6B'."
            )
        self._repo_id = repo_id
        self._pooling = pooling or self._default_pooling(repo_id)
        self._session = session
        self._tokenizer = tokenizer
        self._model_dir = model_dir
        self._max_length = max_length
        self._dimensions: int | None = None
        self._input_names: set[str] | None = None
        self._input_shapes: dict[str, list[Any]] | None = None
        self._input_types: dict[str, str] | None = None
        self._output_index: int | None = None

    @staticmethod
    def _default_pooling(repo_id: str) -> str:
        return "last" if "qwen" in repo_id.lower() else "mean"

    def _snapshot_dir(self) -> str:
        """Download the ONNX subfolder and tokenizer into one snapshot dir.

        Uses ``snapshot_download`` rather than ``hf_hub_download`` of the single
        ONNX file: the latter symlinks ``model.onnx`` into the blob store so its
        sibling ``model.onnx_data`` is not beside it, and onnxruntime refuses to
        follow the data symlink out of the model directory. A snapshot
        materializes the graph and its external-data file together with intact
        relative paths.
        """
        try:
            from huggingface_hub import snapshot_download
        except ImportError as exc:  # pragma: no cover - import guard
            raise ImportError(
                "The 'hf' backend requires huggingface_hub. Install it with "
                "'pip install duckdb-vectors'."
            ) from exc
        return snapshot_download(
            repo_id=self._repo_id,
            allow_patterns=list(_SNAPSHOT_PATTERNS),
        )

    def _resolve_onnx_dir(self, snapshot_dir: str) -> str:
        """Return a real directory holding the ONNX graph + any ``*.onnx_data``.

        Probes ``_ONNX_CANDIDATES`` inside the snapshot, then copies the graph
        and every sibling file into a fresh directory with real (symlink-free)
        files so onnxruntime finds ``model.onnx_data`` beside ``model.onnx``.
        """
        import os
        import shutil
        import tempfile

        onnx_rel = None
        for candidate in _ONNX_CANDIDATES:
            if os.path.exists(os.path.join(snapshot_dir, candidate)):
                onnx_rel = candidate
                break
        if onnx_rel is None:
            raise FileNotFoundError(
                f"No ONNX model found in repo '{self._repo_id}' "
                f"(tried {', '.join(_ONNX_CANDIDATES)})."
            )
        src_dir = os.path.join(snapshot_dir, os.path.dirname(onnx_rel))
        real_dir = tempfile.mkdtemp(prefix="duckdb_vectors_onnx_")
        for name in os.listdir(src_dir):
            source = os.path.join(src_dir, name)
            if os.path.isfile(source) or os.path.islink(source):
                shutil.copy(os.path.realpath(source), os.path.join(real_dir, name))
        return os.path.join(real_dir, os.path.basename(onnx_rel))

    def _build_session(self):
        try:
            import onnxruntime
        except ImportError as exc:  # pragma: no cover - import guard
            raise ImportError(
                "The 'hf' backend requires onnxruntime. Install it with "
                "'pip install duckdb-vectors'."
            ) from exc
        import os

        onnx_path = None
        if self._model_dir is not None:
            for candidate in _ONNX_CANDIDATES:
                path = os.path.join(self._model_dir, candidate)
                if os.path.exists(path):
                    onnx_path = path
                    break
            if onnx_path is None:
                raise FileNotFoundError(
                    f"No ONNX model found under {self._model_dir} "
                    f"(tried {', '.join(_ONNX_CANDIDATES)})."
                )
        else:
            onnx_path = self._resolve_onnx_dir(self._snapshot_dir())
        return onnxruntime.InferenceSession(onnx_path)

    def _build_tokenizer(self):
        try:
            from tokenizers import Tokenizer
        except ImportError as exc:  # pragma: no cover - import guard
            raise ImportError(
                "The 'hf' backend requires tokenizers. Install it with "
                "'pip install duckdb-vectors'."
            ) from exc
        import os

        if self._model_dir is not None:
            path = os.path.join(self._model_dir, "tokenizer.json")
        else:
            path = os.path.join(self._snapshot_dir(), "tokenizer.json")
        tokenizer = Tokenizer.from_file(path)
        tokenizer.enable_truncation(max_length=self._max_length)
        tokenizer.enable_padding()
        return tokenizer

    def _ensure_loaded(self) -> None:
        if self._session is None:
            self._session = self._build_session()
        if self._tokenizer is None:
            self._tokenizer = self._build_tokenizer()
        if self._input_names is None:
            inputs = self._session.get_inputs()
            self._input_names = {inp.name for inp in inputs}
            self._input_shapes = {inp.name: list(inp.shape) for inp in inputs}
            self._input_types = {inp.name: inp.type for inp in inputs}
        if self._output_index is None:
            self._output_index = self._resolve_output_index()

    def _resolve_output_index(self) -> int:
        """Index of the hidden-state output in ``session.run`` results.

        Prefers an output literally named ``last_hidden_state``; otherwise the
        first float output that is neither ``logits`` nor a ``present.*`` cache
        output. Falls back to ``0`` (today's behavior) when no name qualifies —
        covering stubs whose single output is the hidden state.
        """
        outputs = self._session.get_outputs()
        for index, output in enumerate(outputs):
            if output.name == "last_hidden_state":
                return index
        for index, output in enumerate(outputs):
            name = output.name
            if name == "logits" or name.startswith("present."):
                continue
            if "float" in getattr(output, "type", "") or not getattr(
                output, "type", ""
            ):
                return index
        return 0

    @property
    def model_identifier(self) -> str:
        """Identifier folded into the etag."""
        return f"hf:{self._repo_id}"

    @property
    def dimensions(self) -> int:
        """Vector dimensionality, probed once from a single embed."""
        if self._dimensions is None:
            probe = self.embed(["dimension probe"])
            self._dimensions = len(probe[0])
        return self._dimensions

    @staticmethod
    def _numpy_dtype(onnx_type: str):
        """Map an ONNX tensor type string to a numpy dtype (default float32)."""
        import numpy as np

        if "float16" in onnx_type:
            return np.float16
        if "double" in onnx_type:
            return np.float64
        if "int64" in onnx_type:
            return np.int64
        if "int32" in onnx_type:
            return np.int32
        return np.float32

    def _empty_cache_feed(self, name: str, batch: int):
        """Zero-length ``past_key_values.*`` tensor from the declared shape.

        The declared shape is ``[batch, kv_heads, past_sequence, head_dim]``;
        the sequence axis (index 2) is set to zero, and ``kv_heads`` / ``head_dim``
        are read as concrete ints from the graph (never hardcoded). Dynamic
        axes declared as strings are ignored for the concrete dims.
        """
        import numpy as np

        assert self._input_shapes is not None
        assert self._input_types is not None
        shape = self._input_shapes[name]
        kv_heads = shape[1] if isinstance(shape[1], int) else 1
        head_dim = shape[3] if isinstance(shape[3], int) else 1
        dtype = self._numpy_dtype(self._input_types.get(name, ""))
        return np.zeros((batch, kv_heads, 0, head_dim), dtype=dtype)

    def _encode(self, texts: Sequence[str]):
        import numpy as np

        encodings = self._tokenizer.encode_batch(list(texts))
        input_ids = np.asarray([e.ids for e in encodings], dtype=np.int64)
        attention_mask = np.asarray(
            [e.attention_mask for e in encodings], dtype=np.int64
        )
        feeds: dict[str, Any] = {}
        assert self._input_names is not None
        if "input_ids" in self._input_names:
            feeds["input_ids"] = input_ids
        if "attention_mask" in self._input_names:
            feeds["attention_mask"] = attention_mask
        if "token_type_ids" in self._input_names:
            feeds["token_type_ids"] = np.zeros_like(input_ids)
        if "position_ids" in self._input_names:
            position_ids = np.clip(
                np.cumsum(attention_mask, axis=1) - 1, a_min=0, a_max=None
            )
            feeds["position_ids"] = position_ids.astype(np.int64)
        batch = input_ids.shape[0]
        for name in self._input_names:
            if name.startswith("past_key_values"):
                feeds[name] = self._empty_cache_feed(name, batch)
        return feeds, attention_mask

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch, preserving order."""
        if not texts:
            return []
        self._ensure_loaded()
        feeds, attention_mask = self._encode(texts)
        outputs = self._session.run(None, feeds)
        assert self._output_index is not None
        last_hidden_state = outputs[self._output_index]
        if self._pooling == "last":
            pooled = last_token_pool(last_hidden_state, attention_mask)
        else:
            pooled = mean_pool(last_hidden_state, attention_mask)
        vectors = [[float(value) for value in row] for row in pooled]
        if self._dimensions is None and vectors:
            self._dimensions = len(vectors[0])
        return vectors

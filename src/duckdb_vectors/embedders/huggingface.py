"""Core Hugging Face ONNX embedding backend (``hf:<repo-id>``).

Loads any Hugging Face repository that ships ONNX weights and a
``tokenizer.json``, runs inference through the ONNX runtime that fastembed
already vendors (no second runtime, no torch, no transformers), and mean-pools
the last hidden state. Last-token pooling is supported for Qwen3-style models.

Everything heavy is imported lazily inside methods so the module imports
cleanly offline and is unit-testable with a stubbed session and tokenizer.
"""

from collections.abc import Sequence

# Common in-repo locations for the exported ONNX graph, tried in order.
_ONNX_CANDIDATES = (
    "onnx/model.onnx",
    "model.onnx",
    "onnx/model_quantized.onnx",
    "model_quantized.onnx",
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

    @staticmethod
    def _default_pooling(repo_id: str) -> str:
        return "last" if "qwen" in repo_id.lower() else "mean"

    def _download(self, filename: str) -> str:
        try:
            from huggingface_hub import hf_hub_download
        except ImportError as exc:  # pragma: no cover - import guard
            raise ImportError(
                "The 'hf' backend requires huggingface_hub. Install it with "
                "'pip install duckdb-vectors'."
            ) from exc
        return hf_hub_download(repo_id=self._repo_id, filename=filename)

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
            last_error: Exception | None = None
            for candidate in _ONNX_CANDIDATES:
                try:
                    onnx_path = self._download(candidate)
                    break
                except Exception as exc:  # noqa: BLE001 - probe next candidate
                    last_error = exc
            if onnx_path is None:
                raise FileNotFoundError(
                    f"No ONNX model found in repo '{self._repo_id}' "
                    f"(tried {', '.join(_ONNX_CANDIDATES)})."
                ) from last_error
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
            path = self._download("tokenizer.json")
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
            self._input_names = {inp.name for inp in self._session.get_inputs()}

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

    def _encode(self, texts: Sequence[str]):
        import numpy as np

        encodings = self._tokenizer.encode_batch(list(texts))
        input_ids = np.asarray([e.ids for e in encodings], dtype=np.int64)
        attention_mask = np.asarray(
            [e.attention_mask for e in encodings], dtype=np.int64
        )
        feeds = {}
        assert self._input_names is not None
        if "input_ids" in self._input_names:
            feeds["input_ids"] = input_ids
        if "attention_mask" in self._input_names:
            feeds["attention_mask"] = attention_mask
        if "token_type_ids" in self._input_names:
            feeds["token_type_ids"] = np.zeros_like(input_ids)
        return feeds, attention_mask

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch, preserving order."""
        if not texts:
            return []
        self._ensure_loaded()
        feeds, attention_mask = self._encode(texts)
        outputs = self._session.run(None, feeds)
        last_hidden_state = outputs[0]
        if self._pooling == "last":
            pooled = last_token_pool(last_hidden_state, attention_mask)
        else:
            pooled = mean_pool(last_hidden_state, attention_mask)
        vectors = [[float(value) for value in row] for row in pooled]
        if self._dimensions is None and vectors:
            self._dimensions = len(vectors[0])
        return vectors

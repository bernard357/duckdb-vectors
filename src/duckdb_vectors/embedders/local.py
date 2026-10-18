"""Local ONNX embedding backend powered by fastembed.

fastembed ships with the base install (``pip install duckdb-vectors``).
fastembed runs ONNX models with no torch dependency, keeping container images
small and cold starts fast. The default model ``BAAI/bge-small-en-v1.5``
produces 384-dimension vectors — well within the 512-dimension budget.
"""

DEFAULT_MODEL = "BAAI/bge-small-en-v1.5"

# Dimensions for the models we name explicitly. Unknown models are probed once.
_KNOWN_DIMENSIONS = {
    "BAAI/bge-small-en-v1.5": 384,
    "BAAI/bge-base-en-v1.5": 768,
}


class LocalEmbedder:
    """Embed text locally with a fastembed ONNX model.

    The model is loaded lazily on first use so that merely constructing the
    embedder (for example to read ``model_identifier``) stays cheap.
    """

    def __init__(self, model_name: str | None = None) -> None:
        self._model_name = model_name or DEFAULT_MODEL
        self._model = None
        self._dimensions: int | None = _KNOWN_DIMENSIONS.get(self._model_name)

    def _load(self):
        if self._model is None:
            try:
                from fastembed import TextEmbedding
            except ImportError as exc:  # pragma: no cover - import guard
                raise ImportError(
                    "The 'local' backend requires fastembed. Install it with "
                    "'pip install duckdb-vectors'."
                ) from exc
            self._model = TextEmbedding(model_name=self._model_name)
        return self._model

    @property
    def model_identifier(self) -> str:
        """Identifier folded into the etag."""
        return f"local:{self._model_name}"

    @property
    def dimensions(self) -> int:
        """Vector dimensionality, probed once if the model is unknown."""
        if self._dimensions is None:
            probe = self.embed(["dimension probe"])
            self._dimensions = len(probe[0])
        return self._dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch, preserving order."""
        if not texts:
            return []
        model = self._load()
        vectors = [vector.tolist() for vector in model.embed(texts)]
        if self._dimensions is None and vectors:
            self._dimensions = len(vectors[0])
        return vectors

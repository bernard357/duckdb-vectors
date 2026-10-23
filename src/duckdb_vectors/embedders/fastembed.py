"""fastembed ONNX embedding backend.

fastembed ships with the base install (``pip install duckdb-vectors``).
fastembed runs ONNX models with no torch dependency, keeping container images
small and cold starts fast. The default model
``minishlab/potion-multilingual-128M`` is a static Model2Vec model producing
compact 256-dimension multilingual vectors — the smallest vectors and database
while still serving international documentation.
"""

DEFAULT_MODEL = "minishlab/potion-multilingual-128M"

# Dimensions for the models we name explicitly. Unknown models are probed once.
_KNOWN_DIMENSIONS = {
    "minishlab/potion-multilingual-128M": 256,
    "sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2": 384,
    "sentence-transformers/paraphrase-multilingual-mpnet-base-v2": 768,
    "intfloat/multilingual-e5-large": 1024,
    "BAAI/bge-small-en-v1.5": 384,
    "BAAI/bge-base-en-v1.5": 768,
    "BAAI/bge-large-en-v1.5": 1024,
}


class FastembedEmbedder:
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
            # Imported lazily and function-scoped; Python resolves the
            # top-level third-party ``fastembed`` package, not this module.
            try:
                from fastembed import TextEmbedding
            except ImportError as exc:  # pragma: no cover - import guard
                raise ImportError(
                    "The 'fastembed' backend requires fastembed. Install it "
                    "with 'pip install duckdb-vectors'."
                ) from exc
            self._model = TextEmbedding(model_name=self._model_name)
        return self._model

    @property
    def model_identifier(self) -> str:
        """Identifier folded into the etag."""
        return f"fastembed:{self._model_name}"

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

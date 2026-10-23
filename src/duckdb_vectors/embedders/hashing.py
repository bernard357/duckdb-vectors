"""A deterministic, dependency-free embedder.

Useful as a zero-install default and in tests: it needs no model download and
no optional extra, yet produces stable, normalized vectors so cosine distance
behaves. It is not semantically meaningful — swap in ``fastembed`` or a cloud
backend for real relevance.
"""

import math
from hashlib import sha256

DEFAULT_DIMENSIONS = 384


class HashingEmbedder:
    """Hash tokens into a fixed-dimension bag-of-words vector.

    Each token is hashed to a bucket and a sign; buckets accumulate and the
    vector is L2-normalized. Identical text always yields an identical vector.
    """

    def __init__(self, dimensions: int = DEFAULT_DIMENSIONS) -> None:
        self._dimensions = dimensions

    @property
    def model_identifier(self) -> str:
        """Identifier folded into the etag."""
        return f"hashing:{self._dimensions}"

    @property
    def dimensions(self) -> int:
        """Vector dimensionality."""
        return self._dimensions

    def _embed_one(self, text: str) -> list[float]:
        vector = [0.0] * self._dimensions
        for token in text.lower().split():
            digest = sha256(token.encode("utf-8")).digest()
            bucket = int.from_bytes(digest[:4], "big") % self._dimensions
            sign = 1.0 if digest[4] & 1 else -1.0
            vector[bucket] += sign
        norm = math.sqrt(sum(value * value for value in vector))
        if norm == 0.0:
            return vector
        return [value / norm for value in vector]

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch, preserving order."""
        return [self._embed_one(text) for text in texts]

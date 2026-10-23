"""The embedder protocol every backend implements."""

from typing import Protocol, runtime_checkable


@runtime_checkable
class Embedder(Protocol):
    """A text embedding backend.

    Implementations turn strings into fixed-dimension float vectors. The
    ``model_identifier`` and ``dimensions`` feed the etag so a backend change
    invalidates stored vectors.
    """

    @property
    def model_identifier(self) -> str:
        """Stable identifier of the backing model, e.g. ``fastembed:bge-small``."""
        ...

    @property
    def dimensions(self) -> int:
        """Dimensionality of the vectors this backend produces."""
        ...

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed a batch of texts, preserving input order."""
        ...

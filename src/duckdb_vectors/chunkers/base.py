"""The chunker protocol every splitter implements."""

from typing import Protocol, runtime_checkable

from duckdb_vectors.models import Chunk


@runtime_checkable
class Chunker(Protocol):
    """Split a source document into ordered, indexable chunks.

    ``source`` is a stable identifier for the document (typically a relative
    file path). Implementations assign a stable ``chunk_id`` per position so a
    re-index of the same source upserts rather than duplicates.
    """

    def chunk(self, text: str, source: str) -> list[Chunk]:
        """Return the chunks for one document, in document order."""
        ...

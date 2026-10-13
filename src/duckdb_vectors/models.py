"""Core data models for duckdb-vectors."""

from pydantic import BaseModel, Field


class Chunk(BaseModel):
    """A unit of text with metadata, stored and retrieved as one row.

    The ``chunk_id`` is the stable key: re-indexing the same source position
    upserts the same id. ``etag`` fingerprints the embedded text so unchanged
    chunks skip re-embedding.
    """

    chunk_id: str
    text: str
    source: str = ""
    position: int = 0
    title: str = ""
    metadata: dict = Field(default_factory=dict)
    etag: str = ""


class SearchResult(BaseModel):
    """A ranked search hit.

    ``score`` is the fused rank score (higher is better). ``vector_rank`` and
    ``text_rank`` expose the component ranks (1-based) each signal assigned, or
    ``None`` when a signal did not surface the chunk.
    """

    chunk: Chunk
    score: float
    vector_rank: int | None = None
    text_rank: int | None = None

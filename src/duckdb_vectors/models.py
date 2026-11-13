"""Core data models for duckdb-vectors."""

import datetime
import json
from typing import Any

from pydantic import BaseModel, Field, field_validator


def _jsonable(value: Any) -> Any:
    """``json.dumps`` default: ISO 8601 text for dates, ``str()`` otherwise."""
    if isinstance(value, datetime.date):  # datetime is a subclass of date
        return value.isoformat()
    return str(value)


def _string_keys(value: Any) -> Any:
    """Recursively turn mapping keys into strings, as JSON would store them."""
    if isinstance(value, dict):
        return {str(key): _string_keys(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_string_keys(item) for item in value]
    return value


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

    @field_validator("metadata", mode="after")
    @classmethod
    def _json_native_metadata(cls, value: dict) -> dict:
        """Normalize metadata to its JSON round-trip form.

        The indexer compares the live metadata with ``json.loads`` of the
        stored row, so the two must be equal. Dates and datetimes become ISO
        8601 text, any other non-JSON value becomes its ``str()``, and keys
        become strings.
        """
        return json.loads(json.dumps(_string_keys(value), default=_jsonable))


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

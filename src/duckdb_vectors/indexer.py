"""Index a folder into a vector store with ETag reuse.

Drives a :class:`Chunker` to walk the folder — the chunker owns traversal and
decides which files it handles — and embeds only the chunks whose content
changed since the last run. An etag fingerprints each chunk's embedded text
plus the embedder identity; a chunk whose etag already exists reuses the stored
vector instead of paying for a fresh embedding.

Only rows that are new or differ from what is stored (etag, source, position,
title, or metadata) are written, and rows whose ``chunk_id`` no longer appears
in the folder are pruned. Re-indexing an unchanged folder therefore writes
nothing and skips the full-text rebuild. Chunking runs before the store touches
the database, so the file lock covers only the lookups and the writes. A row's
``stamped_at`` records when it was last written.
"""

import datetime
import json
from pathlib import Path

from pydantic import BaseModel

from duckdb_vectors.chunkers.base import Chunker
from duckdb_vectors.chunkers.registry import resolve_chunker
from duckdb_vectors.etag import compute_etag
from duckdb_vectors.models import Chunk
from duckdb_vectors.store import VectorStore


class IndexReport(BaseModel):
    """Outcome of an :func:`index_folder` run.

    ``written`` counts rows actually inserted or replaced: zero when re-indexing
    an unchanged folder, which then leaves the database untouched.
    """

    files: int = 0
    chunks: int = 0
    embedded: int = 0
    reused: int = 0
    written: int = 0
    pruned: int = 0


def index_folder(
    store: VectorStore,
    folder: str | Path,
    chunker: Chunker | None = None,
    recursive: bool = False,
) -> IndexReport:
    """Index every file ``chunker`` handles under ``folder`` into ``store``.

    Only chunks whose etag is new or changed are embedded; unchanged chunks
    reuse their stored vector, and unchanged rows are not rewritten. Chunks for
    files that disappeared are pruned.

    An explicit ``chunker`` instance wins. With no explicit ``chunker``,
    indexing uses the composite chunker, which dispatches each file to the
    registered chunker that claims its MIME type. The chunker owns the folder
    walk; this function wraps it in the run-level ``begin_indexing`` /
    ``end_indexing`` hook pair and guarantees ``end_indexing`` runs even if
    indexing raises.
    """
    chunker = chunker or resolve_chunker("composite")
    folder = Path(folder)
    model_id = store.embedder.model_identifier
    dimension = store.dimensions

    prepared: list[Chunk] = []
    sources: set[str] = set()
    chunker.begin_indexing()
    try:
        for chunk in chunker.chunk_folder(folder, recursive=recursive):
            chunk.etag = compute_etag(chunk.text, model_id, dimension)
            sources.add(chunk.source)
            prepared.append(chunk)
    finally:
        chunker.end_indexing()

    files_seen = getattr(chunker, "_files_seen", len(sources))
    report = IndexReport(files=files_seen, chunks=len(prepared))
    if not prepared:
        report.pruned = _purge_all(store)
        return report

    # Everything above ran without the database. From here the store connects
    # (taking the file lock), so keep the locked work to lookups and writes.
    current = {chunk.chunk_id: chunk for chunk in prepared}
    stored = _stored_signatures(store)
    changed = [
        chunk
        for chunk in current.values()
        if stored.get(chunk.chunk_id) != _signature(chunk)
    ]
    stale = [chunk_id for chunk_id in stored if chunk_id not in current]

    reusable = _reusable_vectors(store, {chunk.etag for chunk in changed})
    to_embed = [chunk for chunk in changed if chunk.etag not in reusable]
    unique_missing = list({chunk.etag: chunk for chunk in to_embed}.values())
    if unique_missing:
        # Embedding needs no database: release the lock while it runs so other
        # sessions can query; the store reconnects lazily for the writes.
        store.close()
        vectors = store.embedder.embed([chunk.text for chunk in unique_missing])
        for chunk, vector in zip(unique_missing, vectors, strict=True):
            reusable[chunk.etag] = vector

    report.embedded = len(unique_missing)
    report.reused = len(prepared) - len(unique_missing)

    stamp = datetime.datetime.now(datetime.UTC)
    store._write_rows(
        [(chunk, chunk.etag, reusable[chunk.etag]) for chunk in changed], stamp
    )
    report.written = len(changed)
    report.pruned = _delete_chunks(store, stale)

    if changed or stale:
        store._rebuild_fts()
    return report


def _signature(chunk: Chunk) -> tuple:
    """The stored fields that decide whether a chunk's row must be rewritten."""
    return (chunk.etag, chunk.source, chunk.position, chunk.title, chunk.metadata)


def _stored_signatures(store: VectorStore) -> dict[str, tuple]:
    """Map each stored ``chunk_id`` to its signature, without loading vectors."""
    rows = store._con.execute(
        "SELECT chunk_id, etag, source, position, title, metadata FROM chunks"
    ).fetchall()
    return {
        row[0]: (row[1], row[2], row[3], row[4], json.loads(row[5]) if row[5] else {})
        for row in rows
    }


def _delete_chunks(store: VectorStore, chunk_ids: list[str]) -> int:
    """Delete rows by ``chunk_id``; return how many were removed."""
    if chunk_ids:
        store._mark_stale()
        store._con.executemany(
            "DELETE FROM chunks WHERE chunk_id = ?",
            [[chunk_id] for chunk_id in chunk_ids],
        )
    return len(chunk_ids)


def _reusable_vectors(store: VectorStore, etags: set[str]) -> dict[str, list]:
    if not etags:
        return {}
    placeholders = ", ".join(["?"] * len(etags))
    rows = store._con.execute(
        f"""
        SELECT etag, ANY_VALUE(embedding)
        FROM chunks WHERE etag IN ({placeholders})
        GROUP BY etag
        """,
        list(etags),
    ).fetchall()
    return {row[0]: row[1] for row in rows}


def _purge_all(store: VectorStore) -> int:
    store._mark_stale()
    removed = store._con.execute("DELETE FROM chunks RETURNING chunk_id").fetchall()
    store._rebuild_fts()
    return len(removed)

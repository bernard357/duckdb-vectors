"""Index a folder of markdown into a vector store with ETag reuse.

Walks markdown files, chunks each with a :class:`Chunker`, and embeds only the
chunks whose content changed since the last run. An etag fingerprints each
chunk's embedded text plus the embedder identity; a chunk whose etag already
exists reuses the stored vector instead of paying for a fresh embedding. After
writing, rows not touched this run (deleted or moved chunks) are purged.
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
    """Outcome of an :func:`index_folder` run."""

    files: int = 0
    chunks: int = 0
    embedded: int = 0
    reused: int = 0
    pruned: int = 0


def _iter_markdown(folder: Path, recursive: bool) -> list[Path]:
    pattern = "**/*.md" if recursive else "*.md"
    return sorted(folder.glob(pattern))


def index_folder(
    store: VectorStore,
    folder: str | Path,
    chunker: Chunker | None = None,
    recursive: bool = False,
) -> IndexReport:
    """Index every markdown file under ``folder`` into ``store``.

    Only chunks whose etag is new or changed are embedded; unchanged chunks
    reuse their stored vector. Chunks for files that disappeared are pruned.

    An explicit ``chunker`` instance wins; otherwise the chunker is resolved
    from the store's ``settings.chunker`` string (``"markdown"`` by default).
    """
    chunker = chunker or resolve_chunker(store.settings.chunker)
    folder = Path(folder)
    model_id = store.embedder.model_identifier
    dimension = store.dimensions

    files = _iter_markdown(folder, recursive)
    prepared: list[Chunk] = []
    for path in files:
        text = path.read_text(encoding="utf-8", errors="replace")
        source = str(path.relative_to(folder))
        for chunk in chunker.chunk(text, source):
            chunk.etag = compute_etag(chunk.text, model_id, dimension)
            prepared.append(chunk)

    report = IndexReport(files=len(files), chunks=len(prepared))
    if not prepared:
        report.pruned = _purge_all(store)
        return report

    wanted = {chunk.etag for chunk in prepared}
    reusable = _reusable_vectors(store, wanted)

    stamp = datetime.datetime.now(datetime.UTC)
    to_embed = [chunk for chunk in prepared if chunk.etag not in reusable]
    unique_missing = list({chunk.etag: chunk for chunk in to_embed}.values())
    if unique_missing:
        vectors = store.embedder.embed([chunk.text for chunk in unique_missing])
        for chunk, vector in zip(unique_missing, vectors, strict=True):
            reusable[chunk.etag] = vector

    report.embedded = len(unique_missing)
    report.reused = len(prepared) - len(unique_missing)

    for chunk in prepared:
        _write_chunk(store, chunk, reusable[chunk.etag], stamp)

    report.pruned = _purge_stale(store, stamp)
    store._rebuild_fts()
    return report


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


def _write_chunk(
    store: VectorStore,
    chunk: Chunk,
    vector: list,
    stamp: datetime.datetime,
) -> None:
    store._con.execute("DELETE FROM chunks WHERE chunk_id = ?", [chunk.chunk_id])
    store._con.execute(
        f"""
        INSERT INTO chunks
            (chunk_id, text, source, position, title, metadata,
             etag, stamped_at, embedding)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?::FLOAT[{store.dimensions}])
        """,
        [
            chunk.chunk_id,
            chunk.text,
            chunk.source,
            chunk.position,
            chunk.title,
            json.dumps(chunk.metadata),
            chunk.etag,
            stamp,
            vector,
        ],
    )


def _purge_stale(store: VectorStore, stamp: datetime.datetime) -> int:
    removed = store._con.execute(
        """
        DELETE FROM chunks
        WHERE stamped_at IS NULL OR stamped_at < ?
        RETURNING chunk_id
        """,
        [stamp],
    ).fetchall()
    return len(removed)


def _purge_all(store: VectorStore) -> int:
    removed = store._con.execute("DELETE FROM chunks RETURNING chunk_id").fetchall()
    store._rebuild_fts()
    return len(removed)

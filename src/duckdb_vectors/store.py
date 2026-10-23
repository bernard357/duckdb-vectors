"""The vector store: one DuckDB file is one index.

Exposes a simplified, S3-Vectors-flavored surface (``put_vectors`` /
``query_vectors``) plus chunk-first conveniences (``add_texts`` / ``query``).
Search is hybrid by default: a brute-force cosine scan and a BM25 full-text
scan are fused with Reciprocal Rank Fusion into one ranked list.

The schema is HNSW-ready — the embedding is a fixed-size ``FLOAT[dim]`` ARRAY
column — but no index is created, so the store opens read-only without the
experimental persistence flag. Brute-force cosine is sub-10ms at the few- to
tens-of-thousands-of-chunks scale this component targets.
"""

import datetime
import json

import duckdb

from duckdb_vectors.config import Settings
from duckdb_vectors.embedders.base import Embedder
from duckdb_vectors.embedders.registry import resolve_embedder
from duckdb_vectors.etag import compute_etag
from duckdb_vectors.models import Chunk, SearchResult

RRF_K = 60

# Built-in ranking templates. Each returns ``chunk_id`` best-first; the store
# collects the ids and applies Reciprocal Rank Fusion. A caller may override
# either template (via Settings or per-call) to add a WHERE for in-scope
# filtering or reshape the ORDER BY for metadata-driven decay. Substituted
# tokens: {table}, {distance} (vector only), {bm25} (text only), {top_k}.
# Bound named parameters available to templates: $embedding (query vector),
# $q (search text), $top_k.
DEFAULT_VECTOR_SQL = """
SELECT chunk_id
FROM {table}
ORDER BY {distance}
LIMIT {top_k}
"""

DEFAULT_TEXT_SQL = """
SELECT chunk_id FROM (
    SELECT chunk_id, {bm25} AS score
    FROM {table}
)
WHERE score IS NOT NULL
ORDER BY score DESC
LIMIT {top_k}
"""


class ReadOnlyError(RuntimeError):
    """Raised when a write is attempted on a read-only store."""


class VectorStore:
    """A DuckDB-backed vector index with hybrid search.

    Open with a backend spec (``"fastembed"``, ``"hashing"``, ``"bedrock:titan"``)
    or an explicit :class:`Embedder`. ``read_only`` opens the file without
    allowing writes — the mode used when a frozen index is baked into an image.
    """

    def __init__(
        self,
        db_path: str | None = None,
        backend: str | None = None,
        embedder: Embedder | None = None,
        read_only: bool | None = None,
        settings: Settings | None = None,
    ) -> None:
        self.settings = settings or Settings(
            db_path=db_path, backend=backend, read_only=read_only
        )
        self.embedder = embedder or resolve_embedder(self.settings.backend)
        self._dimensions = self.embedder.dimensions
        self._read_only = self.settings.read_only
        self._vector_sql = self.settings.vector_sql
        self._text_sql = self.settings.text_sql
        self._con = duckdb.connect(self.settings.db_path, read_only=self._read_only)
        if not self._read_only:
            self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._con.execute(
            f"""
            CREATE TABLE IF NOT EXISTS chunks (
                chunk_id    TEXT NOT NULL,
                text        TEXT NOT NULL,
                source      TEXT NOT NULL DEFAULT '',
                position    INTEGER NOT NULL DEFAULT 0,
                title       TEXT NOT NULL DEFAULT '',
                metadata    TEXT NOT NULL DEFAULT '{{}}',
                etag        TEXT NOT NULL DEFAULT '',
                stamped_at  TIMESTAMP,
                embedding   FLOAT[{self._dimensions}]
            )
            """
        )
        self._con.execute(
            "CREATE INDEX IF NOT EXISTS idx_chunks_chunk_id ON chunks (chunk_id)"
        )
        self._con.execute("CREATE INDEX IF NOT EXISTS idx_chunks_etag ON chunks (etag)")

    def _guard_writable(self) -> None:
        if self._read_only:
            raise ReadOnlyError(
                "This store was opened read-only; writes are not allowed."
            )

    @property
    def dimensions(self) -> int:
        """Embedding dimensionality of this index."""
        return self._dimensions

    def close(self) -> None:
        """Close the underlying connection."""
        self._con.close()

    def __enter__(self) -> "VectorStore":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def _row_to_chunk(self, row: tuple) -> Chunk:
        return Chunk(
            chunk_id=row[0],
            text=row[1],
            source=row[2],
            position=row[3],
            title=row[4],
            metadata=json.loads(row[5]) if row[5] else {},
            etag=row[6],
        )

    def put_vectors(self, chunks: list[Chunk]) -> None:
        """Upsert chunks, embedding their text and (re)building the FTS index.

        Each chunk's etag is computed from its text and the embedder identity.
        Existing rows with the same ``chunk_id`` are replaced.
        """
        self._guard_writable()
        if not chunks:
            return
        vectors = self.embedder.embed([chunk.text for chunk in chunks])
        stamp = datetime.datetime.now(datetime.UTC)
        for chunk, vector in zip(chunks, vectors, strict=True):
            etag = chunk.etag or compute_etag(
                chunk.text, self.embedder.model_identifier, self._dimensions
            )
            self._con.execute("DELETE FROM chunks WHERE chunk_id = ?", [chunk.chunk_id])
            self._con.execute(
                f"""
                INSERT INTO chunks
                    (chunk_id, text, source, position, title, metadata,
                     etag, stamped_at, embedding)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?::FLOAT[{self._dimensions}])
                """,
                [
                    chunk.chunk_id,
                    chunk.text,
                    chunk.source,
                    chunk.position,
                    chunk.title,
                    json.dumps(chunk.metadata),
                    etag,
                    stamp,
                    vector,
                ],
            )
        self._rebuild_fts()

    def add_texts(
        self,
        ids: list[str],
        texts: list[str],
        metadatas: list[dict] | None = None,
        titles: list[str] | None = None,
    ) -> None:
        """Chunk-first convenience: upsert raw texts with ids and metadata."""
        metadatas = metadatas or [{} for _ in texts]
        titles = titles or ["" for _ in texts]
        chunks = [
            Chunk(
                chunk_id=chunk_id,
                text=text,
                title=title,
                metadata=metadata,
            )
            for chunk_id, text, metadata, title in zip(
                ids, texts, metadatas, titles, strict=True
            )
        ]
        self.put_vectors(chunks)

    def get_vectors(self, chunk_ids: list[str]) -> list[Chunk]:
        """Fetch chunks by id, in the order the ids were given."""
        if not chunk_ids:
            return []
        placeholders = ", ".join(["?"] * len(chunk_ids))
        rows = self._con.execute(
            f"""
            SELECT chunk_id, text, source, position, title, metadata, etag
            FROM chunks WHERE chunk_id IN ({placeholders})
            """,
            chunk_ids,
        ).fetchall()
        by_id = {row[0]: self._row_to_chunk(row) for row in rows}
        return [by_id[cid] for cid in chunk_ids if cid in by_id]

    def list_vectors(self, limit: int | None = None) -> list[Chunk]:
        """List stored chunks, ordered by source then position."""
        sql = (
            "SELECT chunk_id, text, source, position, title, metadata, etag "
            "FROM chunks ORDER BY source, position"
        )
        params: list = []
        if limit is not None:
            sql += " LIMIT ?"
            params.append(limit)
        rows = self._con.execute(sql, params).fetchall()
        return [self._row_to_chunk(row) for row in rows]

    def delete_vectors(self, chunk_ids: list[str]) -> int:
        """Delete chunks by id; returns the number of rows removed."""
        self._guard_writable()
        if not chunk_ids:
            return 0
        placeholders = ", ".join(["?"] * len(chunk_ids))
        removed = self._con.execute(
            f"""
            DELETE FROM chunks WHERE chunk_id IN ({placeholders})
            RETURNING chunk_id
            """,
            chunk_ids,
        ).fetchall()
        self._rebuild_fts()
        return len(removed)

    def count(self) -> int:
        """Return the number of stored chunks."""
        row = self._con.execute("SELECT COUNT(*) FROM chunks").fetchone()
        return int(row[0]) if row else 0

    def _metadata_clause(self, filter: dict | None) -> tuple[str, dict]:
        """Build a named-parameter WHERE clause for the convenience filter.

        Returns the SQL fragment and the named-parameter values. Keys are
        namespaced (``f0``, ``f1``) to avoid colliding with the reserved
        ``embedding`` / ``q`` / ``top_k`` names.
        """
        if not filter:
            return "", {}
        clauses = []
        params: dict = {}
        for index, (key, value) in enumerate(filter.items()):
            path_name = f"f{index}p"
            val_name = f"f{index}v"
            clauses.append(f"json_extract_string(metadata, ${path_name}) = ${val_name}")
            params[path_name] = f"$.{key}"
            params[val_name] = str(value)
        return " WHERE " + " AND ".join(clauses), params

    def _distance_expr(self) -> str:
        return (
            f"array_cosine_distance(embedding, $embedding::FLOAT[{self._dimensions}])"
        )

    def _render(self, template: str, **tokens: str) -> str:
        """Substitute ``{token}`` placeholders in a ranking template."""
        rendered = template
        for name, value in tokens.items():
            rendered = rendered.replace("{" + name + "}", value)
        return rendered

    def _vector_ranking(
        self,
        embedding: list[float],
        top_k: int,
        filter: dict | None,
        vector_sql: str | None,
    ) -> list[str]:
        template = vector_sql or self._vector_sql
        params: dict = {"embedding": embedding, "top_k": top_k}
        if template is None:
            # Default path keeps the convenience ``filter`` dict active.
            where, where_params = self._metadata_clause(filter)
            params.update(where_params)
            table = f"chunks{where}"
        else:
            # A custom SELECT owns its WHERE; the ``filter`` dict is ignored.
            table = "chunks"
        sql = self._render(
            template or DEFAULT_VECTOR_SQL,
            table=table,
            distance=self._distance_expr(),
            top_k="$top_k",
        )
        rows = self._con.execute(sql, params).fetchall()
        return [row[0] for row in rows]

    def _text_ranking(
        self,
        text: str,
        top_k: int,
        filter: dict | None,
        text_sql: str | None,
    ) -> list[str]:
        bm25 = "fts_main_chunks.match_bm25(chunk_id, $q)"
        template = text_sql or self._text_sql
        params: dict = {"q": text, "top_k": top_k}
        if template is None:
            where, where_params = self._metadata_clause(filter)
            params.update(where_params)
            table = f"chunks{where}"
        else:
            table = "chunks"
        sql = self._render(
            template or DEFAULT_TEXT_SQL,
            table=table,
            bm25=bm25,
            top_k="$top_k",
        )
        try:
            rows = self._con.execute(sql, params).fetchall()
        except duckdb.Error:
            # No FTS index yet (empty store or never written). Degrade to
            # vector-only by returning no lexical ranking.
            return []
        return [row[0] for row in rows]

    def query_vectors(
        self,
        embedding: list[float],
        top_k: int = 10,
        filter: dict | None = None,
        vector_sql: str | None = None,
    ) -> list[SearchResult]:
        """Pure vector search by a precomputed query embedding.

        ``vector_sql`` overrides the ranking SELECT for this call (precedence:
        per-call > ``Settings`` > built-in default). A custom SELECT owns its
        own WHERE and supersedes the ``filter`` dict.
        """
        ranking = self._vector_ranking(embedding, top_k, filter, vector_sql)
        results = []
        for rank, chunk_id in enumerate(ranking, start=1):
            chunk = self.get_vectors([chunk_id])[0]
            results.append(
                SearchResult(
                    chunk=chunk,
                    score=1.0 / (RRF_K + rank),
                    vector_rank=rank,
                )
            )
        return results

    def query(
        self,
        text: str,
        top_k: int = 10,
        filter: dict | None = None,
        hybrid: bool = True,
        vector_sql: str | None = None,
        text_sql: str | None = None,
    ) -> list[SearchResult]:
        """Hybrid search from a query string (embeds internally).

        Fuses the vector ranking and the BM25 ranking with Reciprocal Rank
        Fusion. Set ``hybrid=False`` for vector-only search.

        ``vector_sql`` / ``text_sql`` override each ranking SELECT for this
        call (precedence: per-call > ``Settings`` > built-in default). A custom
        SELECT owns its own WHERE and supersedes the ``filter`` dict for that
        signal. Templates may reference the search text as the bound parameter
        ``$q`` and use the tokens ``{table}``, ``{distance}``, ``{bm25}``,
        ``{top_k}``.
        """
        embedding = self.embedder.embed([text])[0]
        fetch = max(top_k * 4, top_k)
        vector_ranking = self._vector_ranking(embedding, fetch, filter, vector_sql)
        text_ranking = (
            self._text_ranking(text, fetch, filter, text_sql) if hybrid else []
        )

        vector_rank = {cid: i + 1 for i, cid in enumerate(vector_ranking)}
        text_rank = {cid: i + 1 for i, cid in enumerate(text_ranking)}

        fused: dict[str, float] = {}
        for cid, rank in vector_rank.items():
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + rank)
        for cid, rank in text_rank.items():
            fused[cid] = fused.get(cid, 0.0) + 1.0 / (RRF_K + rank)

        ordered = sorted(fused.items(), key=lambda item: item[1], reverse=True)
        ordered = ordered[:top_k]

        results = []
        for cid, score in ordered:
            chunk = self.get_vectors([cid])[0]
            results.append(
                SearchResult(
                    chunk=chunk,
                    score=score,
                    vector_rank=vector_rank.get(cid),
                    text_rank=text_rank.get(cid),
                )
            )
        return results

    def _rebuild_fts(self) -> None:
        """Rebuild the BM25 index over the chunk text and title.

        The index is built with ``stemmer='none'`` and ``stopwords='none'`` on
        purpose. Semantic (vector) search already covers meaning and synonyms
        across languages; the lexical signal is kept only for what vectors miss
        — exact short tokens such as acronyms, codes, and single-keyword
        queries. Stemming and stopword removal would tie this to one language
        and blur exact tokens without improving relevance, so BM25 here is a
        language-agnostic exact-token match.
        """
        self._con.execute("INSTALL fts")
        self._con.execute("LOAD fts")
        if self.count() == 0:
            try:
                self._con.execute("PRAGMA drop_fts_index('chunks')")
            except duckdb.Error:
                pass
            return
        self._con.execute(
            """
            PRAGMA create_fts_index(
                'chunks', 'chunk_id', 'text', 'title',
                stemmer = 'none', stopwords = 'none', overwrite = 1
            )
            """
        )

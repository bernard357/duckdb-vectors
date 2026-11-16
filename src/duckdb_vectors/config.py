"""Configuration resolution for duckdb-vectors.

Precedence: explicit constructor argument > environment variable
(``DUCKDB_VECTORS_*``) > default.
"""

import os

from pydantic import BaseModel

from duckdb_vectors.tagging import parse_tag_fields

ENV_PREFIX = "DUCKDB_VECTORS_"
DEFAULT_DB_PATH = "vectors.duckdb"
DEFAULT_BACKEND = "fastembed:minishlab/potion-multilingual-128M"
DEFAULT_LOCK_TIMEOUT = 10.0
DEFAULT_TOP_K = 20
_TRUE = {"1", "true", "yes", "on"}


def _env(name: str) -> str | None:
    """Read an environment variable under the project prefix."""
    return os.environ.get(f"{ENV_PREFIX}{name}")


def _resolve_lock_timeout(lock_timeout: float | None) -> float:
    """Return the explicit timeout, else ``DUCKDB_VECTORS_LOCK_TIMEOUT``, else 10."""
    if lock_timeout is not None:
        return lock_timeout
    raw = _env("LOCK_TIMEOUT")
    return float(raw) if raw else DEFAULT_LOCK_TIMEOUT


def _resolve_top_k(top_k: int | None) -> int:
    """Return the explicit value, else ``DUCKDB_VECTORS_TOP_K``, else 20."""
    if top_k is not None:
        return top_k
    raw = _env("TOP_K")
    return int(raw) if raw else DEFAULT_TOP_K


def _resolve_tag_fields(tag_fields: list[str] | str | None) -> list[str]:
    """Return the explicit tag specs, else ``DUCKDB_VECTORS_TAG_FIELDS``.

    Each spec is validated, so a malformed value fails when settings are built
    instead of at the first indexed chunk. The environment variable separates
    specs with ``;``.
    """
    if tag_fields is None:
        tag_fields = _env("TAG_FIELDS")
    fields = parse_tag_fields(tag_fields)
    return [f"{field.label}: {field.path}" for field in fields]


def _resolve_tag_bm25(tag_bm25: bool | None) -> bool:
    """Return the explicit flag, else ``DUCKDB_VECTORS_TAG_BM25``, else False."""
    if tag_bm25 is not None:
        return tag_bm25
    return (_env("TAG_BM25") or "").strip().lower() in _TRUE


class Settings(BaseModel):
    """Resolved settings for a vector store and its embedder.

    Each field falls back from the explicit argument to the matching
    ``DUCKDB_VECTORS_*`` environment variable and finally to a default.
    ``lock_timeout`` bounds how many seconds the store waits for another
    process to release the database file before giving up. ``top_k`` is how
    many results a search returns when the caller does not say. ``tag_fields``
    is the ordered list of ``label: $.path`` tags embedded with each chunk (empty
    means tagging is off), and ``tag_bm25`` also matches those tags in keyword
    search.
    """

    db_path: str
    backend: str
    vector_sql: str | None = None
    text_sql: str | None = None
    lock_timeout: float = DEFAULT_LOCK_TIMEOUT
    top_k: int = DEFAULT_TOP_K
    tag_fields: list[str] = []
    tag_bm25: bool = False

    def __init__(
        self,
        db_path: str | None = None,
        backend: str | None = None,
        vector_sql: str | None = None,
        text_sql: str | None = None,
        lock_timeout: float | None = None,
        top_k: int | None = None,
        tag_fields: list[str] | str | None = None,
        tag_bm25: bool | None = None,
    ) -> None:
        resolved_db = db_path or _env("DB_PATH") or DEFAULT_DB_PATH
        resolved_backend = backend or _env("BACKEND") or DEFAULT_BACKEND
        super().__init__(
            db_path=resolved_db,
            backend=resolved_backend,
            vector_sql=vector_sql or _env("VECTOR_SQL"),
            text_sql=text_sql or _env("TEXT_SQL"),
            lock_timeout=_resolve_lock_timeout(lock_timeout),
            top_k=_resolve_top_k(top_k),
            tag_fields=_resolve_tag_fields(tag_fields),
            tag_bm25=_resolve_tag_bm25(tag_bm25),
        )

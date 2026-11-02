"""Configuration resolution for duckdb-vectors.

Precedence: explicit constructor argument > environment variable
(``DUCKDB_VECTORS_*``) > default.
"""

import os

from pydantic import BaseModel

ENV_PREFIX = "DUCKDB_VECTORS_"
DEFAULT_DB_PATH = "vectors.duckdb"
DEFAULT_BACKEND = "fastembed:minishlab/potion-multilingual-128M"
DEFAULT_LOCK_TIMEOUT = 10.0


def _env(name: str) -> str | None:
    """Read an environment variable under the project prefix."""
    return os.environ.get(f"{ENV_PREFIX}{name}")


def _resolve_lock_timeout(lock_timeout: float | None) -> float:
    """Return the explicit timeout, else ``DUCKDB_VECTORS_LOCK_TIMEOUT``, else 10."""
    if lock_timeout is not None:
        return lock_timeout
    raw = _env("LOCK_TIMEOUT")
    return float(raw) if raw else DEFAULT_LOCK_TIMEOUT


class Settings(BaseModel):
    """Resolved settings for a vector store and its embedder.

    Each field falls back from the explicit argument to the matching
    ``DUCKDB_VECTORS_*`` environment variable and finally to a default.
    ``lock_timeout`` bounds how many seconds the store waits for another
    process to release the database file before giving up.
    """

    db_path: str
    backend: str
    vector_sql: str | None = None
    text_sql: str | None = None
    lock_timeout: float = DEFAULT_LOCK_TIMEOUT

    def __init__(
        self,
        db_path: str | None = None,
        backend: str | None = None,
        vector_sql: str | None = None,
        text_sql: str | None = None,
        lock_timeout: float | None = None,
    ) -> None:
        resolved_db = db_path or _env("DB_PATH") or DEFAULT_DB_PATH
        resolved_backend = backend or _env("BACKEND") or DEFAULT_BACKEND
        super().__init__(
            db_path=resolved_db,
            backend=resolved_backend,
            vector_sql=vector_sql or _env("VECTOR_SQL"),
            text_sql=text_sql or _env("TEXT_SQL"),
            lock_timeout=_resolve_lock_timeout(lock_timeout),
        )

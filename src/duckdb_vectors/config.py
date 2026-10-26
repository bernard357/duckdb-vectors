"""Configuration resolution for duckdb-vectors.

Precedence: explicit constructor argument > environment variable
(``DUCKDB_VECTORS_*``) > default.
"""

import os

from pydantic import BaseModel

ENV_PREFIX = "DUCKDB_VECTORS_"
DEFAULT_DB_PATH = "vectors.duckdb"
DEFAULT_BACKEND = "fastembed:minishlab/potion-multilingual-128M"


def _env(name: str) -> str | None:
    """Read an environment variable under the project prefix."""
    return os.environ.get(f"{ENV_PREFIX}{name}")


class Settings(BaseModel):
    """Resolved settings for a vector store and its embedder.

    Each field falls back from the explicit argument to the matching
    ``DUCKDB_VECTORS_*`` environment variable and finally to a default.
    """

    db_path: str
    backend: str
    vector_sql: str | None = None
    text_sql: str | None = None

    def __init__(
        self,
        db_path: str | None = None,
        backend: str | None = None,
        vector_sql: str | None = None,
        text_sql: str | None = None,
    ) -> None:
        resolved_db = db_path or _env("DB_PATH") or DEFAULT_DB_PATH
        resolved_backend = backend or _env("BACKEND") or DEFAULT_BACKEND
        super().__init__(
            db_path=resolved_db,
            backend=resolved_backend,
            vector_sql=vector_sql or _env("VECTOR_SQL"),
            text_sql=text_sql or _env("TEXT_SQL"),
        )

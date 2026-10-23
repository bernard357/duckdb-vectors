"""Settings precedence: constructor > env > default."""

from unittest import mock

from duckdb_vectors.config import (
    DEFAULT_BACKEND,
    DEFAULT_CHUNKER,
    DEFAULT_DB_PATH,
    Settings,
)


def test_defaults_apply_when_nothing_set():
    with mock.patch.dict("os.environ", {}, clear=True):
        settings = Settings()
    assert settings.db_path == DEFAULT_DB_PATH
    assert settings.backend == DEFAULT_BACKEND
    assert settings.chunker == DEFAULT_CHUNKER
    assert settings.read_only is False


def test_chunker_defaults_to_markdown():
    with mock.patch.dict("os.environ", {}, clear=True):
        assert Settings().chunker == "markdown"
        assert Settings(chunker="markdown").chunker == "markdown"


def test_chunker_environment_overrides_default():
    with mock.patch.dict(
        "os.environ", {"DUCKDB_VECTORS_CHUNKER": "custom"}, clear=True
    ):
        assert Settings().chunker == "custom"


def test_chunker_constructor_overrides_environment():
    with mock.patch.dict(
        "os.environ", {"DUCKDB_VECTORS_CHUNKER": "custom"}, clear=True
    ):
        assert Settings(chunker="markdown").chunker == "markdown"


def test_environment_overrides_default():
    env = {
        "DUCKDB_VECTORS_DB_PATH": "/tmp/env.duckdb",
        "DUCKDB_VECTORS_BACKEND": "hashing",
        "DUCKDB_VECTORS_READ_ONLY": "true",
    }
    with mock.patch.dict("os.environ", env, clear=True):
        settings = Settings()
    assert settings.db_path == "/tmp/env.duckdb"
    assert settings.backend == "hashing"
    assert settings.read_only is True


def test_constructor_overrides_environment():
    env = {
        "DUCKDB_VECTORS_DB_PATH": "/tmp/env.duckdb",
        "DUCKDB_VECTORS_BACKEND": "hashing",
    }
    with mock.patch.dict("os.environ", env, clear=True):
        settings = Settings(db_path="/tmp/explicit.duckdb", backend="fastembed")
    assert settings.db_path == "/tmp/explicit.duckdb"
    assert settings.backend == "fastembed"

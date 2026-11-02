"""Settings precedence: constructor > env > default."""

from unittest import mock

from duckdb_vectors.config import (
    DEFAULT_BACKEND,
    DEFAULT_DB_PATH,
    Settings,
)


def test_defaults_apply_when_nothing_set():
    with mock.patch.dict("os.environ", {}, clear=True):
        settings = Settings()
    assert settings.db_path == DEFAULT_DB_PATH
    assert settings.backend == DEFAULT_BACKEND


def test_environment_overrides_default():
    env = {
        "DUCKDB_VECTORS_DB_PATH": "/tmp/env.duckdb",
        "DUCKDB_VECTORS_BACKEND": "hashing",
    }
    with mock.patch.dict("os.environ", env, clear=True):
        settings = Settings()
    assert settings.db_path == "/tmp/env.duckdb"
    assert settings.backend == "hashing"


def test_constructor_overrides_environment():
    env = {
        "DUCKDB_VECTORS_DB_PATH": "/tmp/env.duckdb",
        "DUCKDB_VECTORS_BACKEND": "hashing",
    }
    with mock.patch.dict("os.environ", env, clear=True):
        settings = Settings(db_path="/tmp/explicit.duckdb", backend="fastembed")
    assert settings.db_path == "/tmp/explicit.duckdb"
    assert settings.backend == "fastembed"


def test_lock_timeout_defaults_to_ten_seconds():
    with mock.patch.dict("os.environ", {}, clear=True):
        settings = Settings()
    assert settings.lock_timeout == 10.0


def test_lock_timeout_reads_environment():
    env = {"DUCKDB_VECTORS_LOCK_TIMEOUT": "2.5"}
    with mock.patch.dict("os.environ", env, clear=True):
        settings = Settings()
    assert settings.lock_timeout == 2.5


def test_lock_timeout_argument_wins_over_environment():
    env = {"DUCKDB_VECTORS_LOCK_TIMEOUT": "2.5"}
    with mock.patch.dict("os.environ", env, clear=True):
        settings = Settings(lock_timeout=0.0)
    assert settings.lock_timeout == 0.0

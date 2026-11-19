"""Settings precedence: constructor > env > default."""

from unittest import mock

import pytest

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


def test_top_k_defaults_to_20(monkeypatch):
    monkeypatch.delenv("DUCKDB_VECTORS_TOP_K", raising=False)
    assert Settings().top_k == 20


def test_top_k_precedence_argument_over_environment(monkeypatch):
    monkeypatch.setenv("DUCKDB_VECTORS_TOP_K", "7")
    assert Settings().top_k == 7
    assert Settings(top_k=3).top_k == 3


def test_tagging_is_off_by_default(monkeypatch):
    monkeypatch.delenv("DUCKDB_VECTORS_TAG_FIELDS", raising=False)
    monkeypatch.delenv("DUCKDB_VECTORS_TAG_BM25", raising=False)
    settings = Settings()
    assert settings.tag_fields == []
    assert settings.tag_bm25 is False


def test_tag_fields_from_environment_and_argument(monkeypatch):
    monkeypatch.setenv("DUCKDB_VECTORS_TAG_FIELDS", "a: $.a; b: $.b.c")
    assert Settings().tag_fields == ["a: $.a", "b: $.b.c"]
    assert Settings(tag_fields=["x: $.x"]).tag_fields == ["x: $.x"]


def test_malformed_tag_field_fails_when_settings_are_built():
    with pytest.raises(ValueError, match="expected 'label: \\$.path'"):
        Settings(tag_fields=["no path here"])


def test_tag_bm25_from_environment_and_argument(monkeypatch):
    monkeypatch.setenv("DUCKDB_VECTORS_TAG_BM25", "true")
    assert Settings().tag_bm25 is True
    assert Settings(tag_bm25=False).tag_bm25 is False

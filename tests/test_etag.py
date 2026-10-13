"""ETag stability and sensitivity."""

from duckdb_vectors.etag import compute_etag


def test_etag_is_deterministic():
    first = compute_etag("hello world", "local:bge", 384)
    second = compute_etag("hello world", "local:bge", 384)
    assert first == second


def test_etag_changes_with_text():
    a = compute_etag("hello world", "local:bge", 384)
    b = compute_etag("hello mars", "local:bge", 384)
    assert a != b


def test_etag_changes_with_model_or_dimension():
    base = compute_etag("hello world", "local:bge", 384)
    other_model = compute_etag("hello world", "bedrock:titan", 384)
    other_dim = compute_etag("hello world", "local:bge", 512)
    assert base != other_model
    assert base != other_dim

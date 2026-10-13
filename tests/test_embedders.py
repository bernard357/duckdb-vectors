"""Embedder registry and the dependency-free hashing backend."""

import pytest

from duckdb_vectors.embedders.hashing import HashingEmbedder
from duckdb_vectors.embedders.registry import (
    register_embedder,
    resolve_embedder,
)


def test_hashing_is_deterministic_and_sized():
    embedder = HashingEmbedder(dimensions=384)
    first = embedder.embed(["the quick brown fox"])
    second = embedder.embed(["the quick brown fox"])
    assert first == second
    assert len(first[0]) == 384


def test_registry_resolves_hashing():
    embedder = resolve_embedder("hashing")
    assert embedder.dimensions == 384
    assert embedder.model_identifier.startswith("hashing:")


def test_registry_resolves_variant():
    embedder = resolve_embedder("hashing:128")
    assert embedder.dimensions == 128


def test_registry_raises_on_unknown():
    with pytest.raises(ValueError, match="Unknown embedding backend"):
        resolve_embedder("does-not-exist")


def test_contributor_registration_hook():
    register_embedder("custom", lambda variant: HashingEmbedder(dimensions=8))
    embedder = resolve_embedder("custom")
    assert embedder.dimensions == 8

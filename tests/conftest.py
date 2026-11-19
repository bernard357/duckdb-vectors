"""Shared fixtures."""

import shutil
from pathlib import Path

import pytest

from duckdb_vectors.config import Settings
from duckdb_vectors.indexer import index_folder
from duckdb_vectors.store import VectorStore

TAGGED = Path(__file__).parent.parent / "fixtures" / "tagged"

PRODUCT_TAGS = ["product: $.product.name"]


@pytest.fixture
def tagged_folder() -> Path:
    """The Markdown corpus with nested frontmatter, arrays, dates, a constant
    tag (``kind``), an identifier-like tag (``doc_id``) and a sparse tag
    (``owner``)."""
    return TAGGED


@pytest.fixture
def tagged_store(tmp_path, tagged_folder):
    """A hashing-embedder store holding the tagged corpus, indexed with tags."""
    settings = Settings(
        db_path=str(tmp_path / "tagged.duckdb"),
        backend="hashing",
        tag_fields=[*PRODUCT_TAGS, "audience: $.audience[*]"],
    )
    store = VectorStore(settings=settings)
    index_folder(store, tagged_folder)
    yield store
    store.close()


@pytest.fixture
def kb(tmp_path) -> Path:
    """A writable copy of the tagged corpus, for the curation scripts to edit."""
    folder = tmp_path / "kb"
    shutil.copytree(TAGGED, folder)
    return folder

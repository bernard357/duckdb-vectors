"""Folder indexer: all-embed, zero-reembed, single-change, prune."""

from duckdb_vectors.indexer import index_folder
from duckdb_vectors.store import VectorStore

DOC_A = "# Alpha\n\nAlpha body content here.\n\n## Sub\n\nSub body text.\n"
DOC_B = "# Beta\n\nBeta body content here.\n"


def _store(tmp_path):
    return VectorStore(db_path=str(tmp_path / "idx.duckdb"), backend="hashing")


def test_first_run_embeds_all(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text(DOC_A)
    (docs / "b.md").write_text(DOC_B)
    store = _store(tmp_path)
    report = index_folder(store, docs)
    assert report.files == 2
    assert report.chunks >= 3
    assert report.embedded == report.chunks
    assert report.reused == 0
    store.close()


def test_second_run_reuses_everything(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text(DOC_A)
    store = _store(tmp_path)
    index_folder(store, docs)
    report = index_folder(store, docs)
    assert report.embedded == 0
    assert report.reused == report.chunks
    store.close()


def test_editing_one_file_reembeds_only_its_chunks(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text(DOC_A)
    (docs / "b.md").write_text(DOC_B)
    store = _store(tmp_path)
    index_folder(store, docs)
    (docs / "b.md").write_text("# Beta\n\nCompletely new beta content.\n")
    report = index_folder(store, docs)
    assert report.embedded >= 1
    assert report.reused >= 1
    store.close()


def test_deleted_file_is_pruned(tmp_path):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "a.md").write_text(DOC_A)
    (docs / "b.md").write_text(DOC_B)
    store = _store(tmp_path)
    index_folder(store, docs)
    before = store.count()
    (docs / "b.md").unlink()
    report = index_folder(store, docs)
    assert report.pruned >= 1
    assert store.count() < before
    store.close()

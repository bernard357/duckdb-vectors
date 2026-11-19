"""Public datasets as Markdown knowledge bases (scripts/benchmark_kb_datasets.py)."""

import json

from development.curation import (
    chunk_folder,
)


def test_anthropic_pages_map_every_published_chunk_to_its_section(
    tmp_path, monkeypatch
):
    from development.datasets import Dataset, Query
    from duckdb_vectors.models import Chunk
    from scripts import benchmark_kb_datasets as kb_datasets

    meta = {"repo_name": "acme/lib", "directory": "/src", "extension": "py"}
    code = "def handler():\n    return '``` not a fence'\n"
    chunks = [
        Chunk(
            chunk_id=f"doc:{i}",
            text=f"{code}# part {i}",
            source="/src/app.py",
            position=i,
            title="app.py",
            metadata=meta,
        )
        for i in range(3)
    ]
    dataset = Dataset(
        name="anthropic-codebases",
        chunks=chunks,
        queries=[Query(text="what does handler return", golden=["doc:1", "doc:2"])],
        sources={},
    )
    monkeypatch.setattr(kb_datasets, "load_anthropic", lambda cache: dataset)
    kb_datasets.prepare_anthropic(tmp_path / "pages", tmp_path / "work")
    page = (tmp_path / "pages" / "src" / "app.py.md").read_text("utf-8")
    assert page.startswith("---\nrepo: acme/lib\n") and "````python" in page
    folder_chunks = chunk_folder(tmp_path / "pages")
    assert [c.text for c in folder_chunks] == [c.text for c in chunks]
    validation = json.loads((tmp_path / "work" / "validation.json").read_text())
    item = validation["items"][0]
    assert item["stratum"] == "native"
    assert item["alternatives"] == [
        [folder_chunks[1].chunk_id],
        [folder_chunks[2].chunk_id],
    ]

"""Offline section contexts (scripts/kb_context.py), with a scripted model."""

import gzip
import json

from development.curation import (
    AnswerCache,
    chunk_folder,
)
from development.kb_testing import TAGGED as FIXTURES
from development.kb_testing import fake_model
from duckdb_vectors.chunkers.markdown import MarkdownChunker
from duckdb_vectors.tagging import parse_tag_fields, project_many
from scripts import (
    kb_context,
)


def test_kb_context_writes_section_contexts_that_the_projection_reads(kb, tmp_path):
    model = fake_model()
    cache = AnswerCache(tmp_path / "cache", model.usage)
    contexts = kb_context.contextualize_folder(kb, model, cache, workers=2)
    calls = model.usage.calls
    assert calls == sum(len(page) for page in contexts.values())
    assert kb_context.apply_contexts(kb, contexts) == len(contexts)
    # A second run is served from the cache and changes nothing.
    again = kb_context.contextualize_folder(kb, model, cache, workers=2)
    assert again == contexts and model.usage.calls == calls
    assert kb_context.apply_contexts(kb, again) == 0
    chunks = chunk_folder(kb)
    tags = project_many(
        [c.metadata for c in chunks], parse_tag_fields(["context: $.retrieval.context"])
    )
    assert all(t and t[0][1].startswith("Context about") for t in tags)
    for chunk in chunks:
        assert "sections" not in chunk.metadata


def test_kb_context_keeps_other_section_metadata():
    front = {
        "sections": {"a": {"owner": "bob", "retrieval": {"context": "old", "x": 1}}}
    }
    merged = kb_context.merge_sections(front, {"a": "new", "b": "other"})
    assert merged == {
        "a": {"owner": "bob", "retrieval": {"x": 1, "context": "new"}},
        "b": {"retrieval": {"context": "other"}},
    }


def test_kb_context_sections_match_the_chunker():
    text = (FIXTURES / "rotate-keys.md").read_text(encoding="utf-8")
    sections = kb_context.page_sections(MarkdownChunker(), text)
    paths = {c.metadata["section_path"] for c in MarkdownChunker().chunk(text, "x")}
    assert set(sections) == paths


def test_kb_context_window_keeps_the_opening_and_the_neighbourhood():
    ordered = [f"section {i} " + "x" * 300 for i in range(10)]
    document = "OPENING " + "\n\n".join(ordered)
    text = kb_context.window(document, ordered, 5, 800)
    assert text.startswith("OPENING")
    assert "section 5" in text and "section 9" not in text
    assert len(text) < 900


def test_kb_context_moves_a_legacy_llm_context_to_retrieval():
    front = {
        "sections": {
            "a": {"llm": {"context": "old"}},
            "b": {"llm": {"context": "old", "note": "kept"}},
        }
    }
    merged = kb_context.merge_sections(front, {"a": "new", "b": "new"})
    assert merged == {
        "a": {"retrieval": {"context": "new"}},
        "b": {"llm": {"note": "kept"}, "retrieval": {"context": "new"}},
    }


def test_kb_context_applies_a_pack_without_a_model(kb, tmp_path):
    model = fake_model()
    cache = AnswerCache(tmp_path / "cache", model.usage)
    contexts = kb_context.contextualize_folder(kb, model, cache, workers=2)
    pack = tmp_path / "pack.json.gz"
    pack.write_bytes(gzip.compress(json.dumps({"contexts": contexts}).encode()))
    work = tmp_path / "work"
    args = [str(kb), "--work", str(work), "--from-pack", str(pack)]
    assert kb_context.main(args) == 0
    assert json.loads((work / "context-report.json").read_text())["pages_changed"]
    tags = project_many(
        [c.metadata for c in chunk_folder(kb)],
        parse_tag_fields(["context: $.retrieval.context"]),
    )
    assert all(t and t[0][1].startswith("Context about") for t in tags)
    # A pack that names a section the pages do not have writes nothing.
    page = next(iter(contexts))
    contexts[page]["no-such-section"] = "stale"
    pack.write_bytes(gzip.compress(json.dumps({"contexts": contexts}).encode()))
    assert kb_context.main(args) == 1

"""Question generation and the engineering-brain dataset, with a fake model."""

import json

from benchmarks.tagged_retrieval.datasets import parse_engineering_brain
from benchmarks.tagged_retrieval.queries import (
    generate,
    longest_copied_run,
    sample_chunks,
)
from duckdb_vectors.models import Chunk

PASSAGE = " ".join(f"alpha{i} beta{i} gamma{i}" for i in range(30))  # 90 words


def chunk(chunk_id, text=PASSAGE, topic="podman", source="a.md"):
    return Chunk(chunk_id=chunk_id, text=text, source=source, metadata={"topic": topic})


def test_longest_copied_run_counts_consecutive_shared_words():
    passage = "the quick brown fox jumps over the lazy dog"
    assert longest_copied_run("what does the quick brown fox do", passage) == 4
    assert longest_copied_run("a slow red cat", passage) == 0
    assert longest_copied_run("", passage) == 0


def test_sample_keeps_only_long_enough_chunks_and_is_seeded():
    chunks = [chunk(f"c{i}") for i in range(10)] + [chunk("short", "too short")]
    picked = sample_chunks(chunks, 4, seed=1)
    assert len(picked) == 4 and "short" not in {c.chunk_id for c in picked}
    assert picked == sample_chunks(chunks, 4, seed=1)


def test_generate_reports_what_it_drops_and_caches_answers(tmp_path):
    chunks = [chunk("c1"), chunk("c2", topic="lambda")]
    calls = []

    def invoke(system, user):
        calls.append(user)
        return json.dumps(
            {
                "questions": [
                    "How do I run a podman container?",
                    "How do I run a podman container?",
                    "alpha0 beta0 gamma0 alpha1 beta1 gamma1 what",
                ]
            }
        )

    kept, report = generate(chunks, invoke, tmp_path / "cache", workers=1)
    # Same passage text for both chunks: the second call is a cache hit.
    assert len(calls) == 1
    assert report == {
        "chunks": 2,
        "questions": 6,
        "duplicates": 3,
        "copied_too_much": 2,
        "mention_topic": 1,
        "kept": 1,
    }
    assert [(k.chunk_id, k.question) for k in kept] == [
        ("c1", "How do I run a podman container?")
    ]


def test_engineering_brain_merges_derived_tags_and_ignores_unknown_chunks():
    chunks = [chunk("a.md:0"), chunk("b.md:0", source="b.md")]
    derived = {
        "a.md": {"programming_language": "Python", "aws_service": None},
        "b.md": {"operational_domain": "data"},
    }
    generated = {
        "prompt_version": 1,
        "model": "fake",
        "quality": {"kept": 3},
        "queries": [
            {"question": "q1", "chunk_id": "a.md:0"},
            {"question": "q2", "chunk_id": "gone.md:0"},
        ],
    }
    dataset = parse_engineering_brain(chunks, derived, generated)
    assert dataset.chunks[0].metadata["llm"] == {"programming_language": "Python"}
    assert dataset.chunks[1].metadata["llm"] == {"operational_domain": "data"}
    assert [(q.text, q.golden) for q in dataset.queries] == [("q1", ["a.md:0"])]
    assert dataset.quality == {"kept": 3, "questions_used": 1}
    assert dataset.default_tag_fields[0] == "topic: $.topic"
    assert len(dataset.default_tag_fields) == 7

"""The tagged-retrieval benchmark harness, offline on a tiny synthetic set."""

import json

import pytest

from benchmarks.tagged_retrieval import metrics
from benchmarks.tagged_retrieval.datasets import parse_anthropic
from benchmarks.tagged_retrieval.run import (
    CONDITIONS,
    build_store,
    run_benchmark,
    summarize,
)


def test_score_query_is_the_share_of_golden_chunks_found():
    assert metrics.score_query(["a", "b", "c"], ["a"]) == 1.0
    assert metrics.score_query(["a", "b"], ["a", "z"]) == 0.5
    assert metrics.score_query([], ["a"]) == 0.0
    with pytest.raises(ValueError):
        metrics.score_query(["a"], [])


def test_reciprocal_rank():
    assert metrics.reciprocal_rank(["x", "a"], ["a"]) == 0.5
    assert metrics.reciprocal_rank(["x"], ["a"]) == 0.0


def test_paired_bootstrap_is_deterministic_and_brackets_the_difference():
    base = [0.0, 0.0, 1.0, 0.0] * 10
    other = [1.0, 0.0, 1.0, 1.0] * 10
    first = metrics.paired_bootstrap(base, other, resamples=500, seed=1)
    assert first == metrics.paired_bootstrap(base, other, resamples=500, seed=1)
    diff, low, high = first
    assert diff == pytest.approx(0.5)
    assert low <= diff <= high
    with pytest.raises(ValueError):
        metrics.paired_bootstrap([1.0], [1.0, 0.0])


def test_mcnemar_exact_p_value():
    assert metrics.mcnemar([True, False], [True, False]) == (0, 0, 1.0)
    gained, lost, p = metrics.mcnemar([False] * 9 + [True], [True] * 9 + [False])
    assert (gained, lost) == (9, 1)
    assert p == pytest.approx(2 * (1 + 10) / 1024)


CHUNKS = [
    {
        "doc_id": "doc_1",
        "original_uuid": f"u{n}",
        "content": "x",
        "chunks": [
            {"chunk_id": f"doc_{n}_chunk_{i}", "original_index": i, "content": text}
            for i, text in enumerate(texts)
        ],
    }
    for n, texts in enumerate(
        [
            ["alpha needle one", "alpha filler"],
            ["beta needle two", "beta filler"],
            ["gamma needle three"],
        ],
        start=1,
    )
]


def _evaluation():
    rows = []
    for n, (text, index) in enumerate(
        [("alpha needle one", 0), ("beta needle two", 0), ("gamma needle three", 0)],
        start=1,
    ):
        rows.append(
            {
                "query": text,
                "golden_chunk_uuids": [[f"u{n}", index]],
                "golden_documents": [
                    {
                        "uuid": f"u{n}",
                        "meta": {
                            "doctype": "codebase",
                            "relative_path": f"/repo{n}/src/file{n}.rs",
                            "repo_name": f"org/repo{n}",
                            "num_chunks": 2,
                            "source": "codebases/jsonl",
                        },
                    }
                ],
            }
        )
    return "\n".join(json.dumps(row) for row in rows)


@pytest.fixture
def dataset():
    return parse_anthropic(json.dumps(CHUNKS), _evaluation(), {"commit": "test"})


def test_parse_joins_native_tags_and_golden_chunks(dataset):
    assert len(dataset.chunks) == 5
    first = dataset.chunks[0]
    assert first.chunk_id == "u1:0"
    assert first.metadata["repo_name"] == "org/repo1"
    assert first.metadata["directory"] == "/repo1/src"
    assert first.metadata["extension"] == "rs"
    assert [q.golden for q in dataset.queries] == [["u1:0"], ["u2:0"], ["u3:0"]]


def test_parse_rejects_a_golden_chunk_missing_from_the_corpus():
    rows = json.loads("[" + ",".join(_evaluation().splitlines()) + "]")
    rows[0]["golden_chunk_uuids"] = [["u1", 9]]
    evaluation = "\n".join(json.dumps(row) for row in rows)
    with pytest.raises(ValueError, match="missing from the corpus"):
        parse_anthropic(json.dumps(CHUNKS), evaluation)


def test_bm25_only_condition_keeps_raw_embeddings(tmp_path, dataset):
    fields = ["repo: $.repo_name"]
    stores = {
        name: build_store(dataset, tmp_path / f"{name}.duckdb", "hashing", name, fields)
        for name in CONDITIONS
    }
    vectors = {}
    for name, store in stores.items():
        rows = store._con.execute(
            "SELECT chunk_id, embedding, tags FROM chunks ORDER BY chunk_id"
        ).fetchall()
        vectors[name] = {row[0]: (list(row[1]), row[2]) for row in rows}
        store.close()
    assert vectors["tags_bm25"]["u1:0"][0] == vectors["baseline"]["u1:0"][0]
    assert vectors["tags_embedding"]["u1:0"][0] != vectors["baseline"]["u1:0"][0]
    assert vectors["tags_bm25"]["u1:0"][1] == "repo: org/repo1"
    assert vectors["baseline"]["u1:0"][1] == ""


def test_run_benchmark_reports_every_condition_and_comparison(dataset):
    report = run_benchmark(dataset, "hashing", ["repo: $.repo_name"], ks=(1, 3))
    assert set(report["conditions"]) == set(CONDITIONS)
    baseline = report["conditions"]["baseline"]
    assert baseline["hybrid"]["3"]["pass"] == 1.0
    compared = report["conditions"]["tags_both"]["versus_baseline"]["hybrid"]["3"]
    assert {"pass_difference", "ci95", "mcnemar_p"} <= set(compared)
    assert report["dataset"]["sources"] == {"commit": "test"}
    assert "tags_bm25" in summarize(report)


@pytest.fixture
def cl100k():
    tiktoken = pytest.importorskip("tiktoken")
    try:
        return tiktoken.get_encoding("cl100k_base")
    except Exception:  # no network and no cached encoding
        pytest.skip("cl100k_base encoding is not available offline")


def _filing(words: int) -> list[dict]:
    text = " ".join(f"word{i}" for i in range(words))
    meta = {
        "company_name": "ACME CORP",
        "form_type": "10-K",
        "period_of_report": "2024-09-28",
    }
    return [{"text": text, "metadata": {"metadata": meta, "section": "Item 1"}}]


def test_ragmate_rechunks_like_the_dataset_and_reports_quality(cl100k):
    from benchmarks.tagged_retrieval.datasets import parse_ragmate

    forms = {"ACME_2024_10-K_chunks.json": _filing(400)}
    dataset = parse_ragmate(forms, [], {})
    chunk_ids = [chunk.chunk_id for chunk in dataset.chunks]
    assert len(chunk_ids) >= 2
    assert all("_sec_" in chunk_id for chunk_id in chunk_ids)
    first = dataset.chunks[0]
    assert first.metadata["company_name"] == "ACME CORP"
    assert first.metadata["fiscal_year"] == "2024"
    assert first.metadata["ticker"] == "ACME"
    assert first.metadata["section"] == "Item 1"

    truth = [
        {
            "question": "q1",
            "cited_chunk_ids": [chunk_ids[0], "chunk_id: " + chunk_ids[1], "nope"],
            "cited_chunks": [{"id": chunk_ids[0], "text": first.text}],
        },
        {"question": "q2", "cited_chunk_ids": ["chunk_id"], "cited_chunks": []},
    ]
    dataset = parse_ragmate(forms, truth, {})
    assert [q.golden for q in dataset.queries] == [chunk_ids[:2]]
    assert dataset.quality == {
        "questions": 2,
        "cited": 4,
        "cited_repaired": 1,
        "cited_dropped": 2,
        "texts_checked": 1,
        "texts_identical": 1,
        "questions_used": 1,
    }
    assert dataset.default_tag_fields[0] == "company: $.company_name"


def test_score_alternatives_counts_evidence_items_not_chunks():
    groups = [["a", "b"], ["c"]]
    assert metrics.score_alternatives(["b", "x"], groups) == 0.5
    assert metrics.score_alternatives(["a", "b", "c"], groups) == 1.0
    assert metrics.score_alternatives([], groups) == 0.0
    with pytest.raises(ValueError):
        metrics.score_alternatives(["a"], [["a"], []])


def _article(url, title, words):
    return {
        "title": title,
        "author": "None",
        "source": "Wire",
        "published_at": "2023-11-27T08:45:59+00:00",
        "category": "business",
        "url": url,
        "body": " ".join(words),
    }


def test_multihop_maps_facts_to_every_chunk_that_holds_them():
    from benchmarks.tagged_retrieval.datasets import parse_multihop

    words = [f"w{i}" for i in range(700)]
    corpus = [_article("u1", "One", words), _article("u2", "Two", ["short", "text"])]
    in_overlap = " ".join(words[270:290])  # in windows 0 and 1 (overlap 40)
    questions = [
        {
            "query": "q1",
            "question_type": "inference_query",
            "evidence_list": [
                {"url": "u1", "fact": in_overlap},
                {"url": "u2", "fact": "short  text"},
                {"url": "u2", "fact": "absent fact"},
            ],
        },
        {"query": "q2", "question_type": "null_query", "evidence_list": []},
        {
            "query": "q3",
            "question_type": "temporal_query",
            "evidence_list": [{"url": "u1", "fact": "not there"}],
        },
    ]
    dataset = parse_multihop(corpus, questions)
    query = dataset.queries[0]
    assert len(dataset.queries) == 1
    assert query.alternatives == [["u1#0", "u1#1"], ["u2#0"]]
    assert query.golden == ["u1#0", "u1#1", "u2#0"]
    assert dataset.chunks[0].metadata == {
        "title": "One",
        "source": "Wire",
        "category": "business",
        "published_at": "2023-11-27T08:45:59+00:00",
    }
    assert dataset.quality == {
        "questions": 3,
        "null_queries": 1,
        "evidence": 4,
        "evidence_dropped": 2,
        "evidence_in_several_chunks": 1,
        "questions_used": 1,
    }

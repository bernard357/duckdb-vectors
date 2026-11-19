"""The knowledge-base curation scripts (scripts/kb_*.py), with a fake model."""

import json
import shutil
from pathlib import Path

import pytest

from duckdb_vectors.chunkers.markdown import MarkdownChunker
from duckdb_vectors.tagging import parse_tag_fields, project_many
from scripts import (
    kb_common,
    kb_context,
    kb_evaluate,
    kb_metrics,
    kb_tags,
    kb_validation,
)
from scripts.kb_common import (
    PROMPTS,
    AnswerCache,
    Model,
    chunk_folder,
    load_prompt,
    read_front_matter,
    set_front_matter_key,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "tagged"
HASHING = "hashing"


@pytest.fixture
def kb(tmp_path):
    folder = tmp_path / "kb"
    shutil.copytree(FIXTURES, folder)
    return folder


def fake_invoke(system: str, blocks: list[dict], max_tokens: int) -> str:
    """Answers each prompt in the shape it asks for, from the text it gets."""
    text = "\n".join(block.get("text", "") for block in blocks)
    if "situate this chunk" in text:
        chunk = text.split("<chunk>")[1].split("</chunk>")[0].split()
        return "Context about " + " ".join(chunk[:3])
    if "Propose at most" in text:
        return json.dumps(
            {
                "fields": [
                    {
                        "name": "product",
                        "description": "The product.",
                        "values": ["Beacon", "Cedar", "Other"],
                        "example_queries": [],
                        "lost_context": "procedures",
                    },
                    {
                        "name": "mood",
                        "description": "Unstable.",
                        "values": "free",
                    },
                ],
                "rejected": [{"name": "title", "reason": "unique"}],
            }
        )
    if "Schema (field" in text:
        product = "Cedar" if "Cedar" in text else "Beacon" if "Beacon" in text else None
        reverse = text.index("- mood") < text.index("- product")
        return json.dumps({"product": product, "mood": "calm" if reverse else "busy"})
    if "reference answer taken from the passage" in text:
        words = text.split("Passage:")[1].split()
        return json.dumps(
            {
                "items": [
                    {"question": f"what about {words[0]} please", "answer": "a"},
                    {"question": " ".join(words[:8]), "answer": "copied"},
                ]
            }
        )
    if "passage A answers and passage B does not" in text:
        words = text.split("Passage A:")[1].split()
        return json.dumps({"question": f"which {words[1]} applies", "answer": "x"})
    if "Grade the passage" in text:
        question = text.split("Question:")[1].split("Reference answer")[0]
        passage = text.split("Candidate passage")[1]
        shared = set(question.lower().split()) & set(passage.lower().split())
        return json.dumps({"grade": 2 if len(shared) > 2 else 0, "reason": ""})
    raise AssertionError(f"unexpected prompt: {text[:80]}")


def fake_model() -> Model:
    return Model("fake", invoke=fake_invoke)


ALL_PROMPTS = [
    ("contextual-header", 1),
    ("schema-discovery", 2),
    ("tagging", 2),
    ("query-local", 1),
    ("query-contextual", 1),
    ("relevance-judge", 1),
]


@pytest.mark.parametrize(("name", "version"), ALL_PROMPTS)
def test_prompts_declare_exactly_the_inputs_they_use(name, version):
    prompt = load_prompt(name, version)
    assert (PROMPTS / f"{name}.v{version}.md").exists()
    assert prompt.name == name
    assert prompt.placeholders() == set(prompt.inputs)
    assert prompt.system


def test_leakage_guard_of_the_prompts():
    for name in ("query-local", "query-contextual", "relevance-judge"):
        prompt = load_prompt(name)
        assert prompt.sees_tags is False
        assert not {"tags", "schema", "metadata", "context"} & set(prompt.inputs)
    for name, version in (("tagging", 2), ("contextual-header", 1)):
        assert load_prompt(name, version).sees_queries is False
    # Schema discovery sees dev queries by design; the script passes only those.
    assert load_prompt("schema-discovery", 2).sees_queries is True


def test_render_requires_every_input():
    prompt = load_prompt("query-local")
    assert "Refunds" in prompt.render(passage="Refunds take 30 days.", count="2")
    with pytest.raises(ValueError, match="missing inputs"):
        prompt.render(passage="x")


def test_set_front_matter_key_rewrites_only_that_key():
    page = "---\ntitle: A\nretrieval:\n  old: 1\nowner: bob\n---\n# A\n\nBody.\n"
    updated = set_front_matter_key(page, "retrieval", {"product": "Cedar"})
    assert read_front_matter(updated) == {
        "title": "A",
        "owner": "bob",
        "retrieval": {"product": "Cedar"},
    }
    assert updated.endswith("# A\n\nBody.\n")
    assert read_front_matter(set_front_matter_key(updated, "retrieval", None)) == {
        "title": "A",
        "owner": "bob",
    }
    assert set_front_matter_key("# A\n", "k", 1).startswith("---\nk: 1\n---\n")


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


def test_kb_tags_discovers_cleans_tags_twice_and_applies(kb, tmp_path):
    model = fake_model()
    schema = kb_tags.discover(kb, model, ["refund for Cedar"], size=4)
    product = schema.fields[0]
    assert product.values == ["Beacon", "Cedar"]
    assert schema.removed_values == {"product": ["Other"]}
    cache = AnswerCache(tmp_path / "cache", model.usage)
    tags, agreement = kb_tags.tag_folder(kb, schema, model, cache, workers=2)
    assert agreement["product"] == 1.0
    assert agreement["mood"] == 0.0  # the two passes disagree on every page
    assert all(values["mood"] is None for values in tags.values())
    changed = kb_tags.apply_tags(kb, tags, ["product"])
    assert changed == sum(1 for values in tags.values() if values["product"])
    front = read_front_matter((kb / "deploy-runbook.md").read_text("utf-8"))
    assert front["retrieval"] == {"product": "Cedar"}
    assert len(kb_tags.review_rows(tags, ["product"])) == len(tags)


def test_kb_tags_existing_fields_skip_reserved_keys(kb):
    fields = kb_tags.existing_fields(kb_tags.pages(kb))
    assert "title" in fields and "retrieval" not in fields and "sections" not in fields


def test_kb_tags_clean_value_rejects_sentinels_and_long_free_text():
    field = kb_tags.Field(name="x", description="", values="free")
    assert kb_tags.clean_value(field, "Other") is None
    assert kb_tags.clean_value(field, "a b c d e") is None
    assert kb_tags.clean_value(field, "Cedar") == "Cedar"


def _validation(kb, tmp_path):
    model = fake_model()
    cache = AnswerCache(tmp_path / "vcache", model.usage)
    validation = kb_validation.ValidationSet(corpus="tagged")
    chunks = chunk_folder(kb)
    for chunk in chunks:  # fixture chunks are short; make them eligible
        chunk.text = (chunk.text + " ") * 12
    return validation, chunks, model, cache


def test_validation_local_drops_copies_and_records_origin(kb, tmp_path):
    validation, chunks, model, cache = _validation(kb, tmp_path)
    report = kb_validation.generate_local(validation, chunks, model, cache, size=4)
    assert report["copied_too_much"] == 4
    assert report["kept"] == len(validation.items) == 4
    for item in validation.items:
        assert item.stratum == "local" and len(item.origin) == 1


def test_validation_contextual_pairs_come_from_different_pages(kb, tmp_path):
    validation, chunks, model, cache = _validation(kb, tmp_path)
    kb_validation.generate_contextual(
        validation, kb, chunks, model, cache, size=3, backend=HASHING
    )
    by_id = {c.chunk_id: c for c in chunks}
    assert validation.items
    for item in validation.items:
        assert item.stratum == "contextual"
        assert by_id[item.confuser].source != item.source


def test_validation_split_is_by_page_and_frozen(kb, tmp_path):
    validation, chunks, model, cache = _validation(kb, tmp_path)
    kb_validation.generate_local(validation, chunks, model, cache, size=8)
    split = kb_validation.freeze_split(validation, dev_share=0.5)
    by_page: dict[str, set] = {}
    for item in validation.items:
        by_page.setdefault(item.source, set()).add(item.split)
    assert all(len(splits) == 1 for splits in by_page.values())
    assert split["dev"] + split["test"] == len(validation.items)
    with pytest.raises(ValueError, match="frozen"):
        kb_validation.freeze_split(validation, dev_share=0.3)
    assert kb_validation.freeze_split(validation, dev_share=0.5) == split


def test_validation_pool_judge_audit_and_kappa(kb, tmp_path):
    validation, chunks, model, cache = _validation(kb, tmp_path)
    kb_validation.generate_local(validation, chunks, model, cache, size=4)
    kb_validation.import_queries(validation, ["refund window", "refund window", ""])
    assert sum(item.stratum == "real" for item in validation.items) == 1
    kb_validation.pool(validation, chunks, [HASHING], ["product: $.product.name"], 5)
    for item in validation.items:
        assert set(item.origin) <= set(item.pooled)
    report = kb_validation.judge(validation, chunks, model, cache, workers=2)
    assert report["judged"] == sum(len(item.pooled) for item in validation.items)
    for item in validation.items:
        assert item.golden == [c for c in item.pooled if item.grades[c] >= 1]
    rows = kb_validation.audit_rows(validation, chunks, size=4)
    assert rows and all(row["human_grade"] == "" for row in rows)
    sheet = tmp_path / "audit.yaml"
    kb_common.write_sheet(
        sheet,
        kb_validation.audit_sheet(rows),
        kb_validation.AUDIT_HEADER,
        kb_validation.AUDIT_HINTS,
    )
    text = sheet.read_text(encoding="utf-8")
    assert "judge_grade" not in text and "human_grade:  # 0, 1 or 2" in text
    # The person fills the blanks in place; a "no" still counts as rated.
    grades = {(row["item"], row["chunk"]): row["judge_grade"] for row in rows}
    records = kb_common.read_sheet(sheet)
    for number, record in enumerate(records):
        record["question_valid"] = number > 0  # YAML reads yes / no as booleans
        for passage in record["passages"]:
            passage["human_grade"] = grades[(record["item"], passage["chunk"])]
    graded = kb_validation.sheet_rows(records)
    for row in graded:
        row["judge_grade"] = grades[(row["item"], row["chunk"])]
    result = kb_validation.agreement(graded)
    assert result["rows"] == len(rows) and result["kappa_binary"] == 1.0
    assert result["questions_rated"] == len(records)
    assert result["questions_valid"] == round(1 - 1 / len(records), 3)


def test_cohen_kappa():
    assert kb_validation.cohen_kappa([0, 1, 2, 2], [0, 1, 2, 2]) == 1.0
    assert kb_validation.cohen_kappa([0, 0, 1, 1], [0, 1, 0, 1]) == 0.0


def test_kb_evaluate_runs_every_condition_per_stratum(kb, tmp_path):
    model = fake_model()
    kb_context.apply_contexts(
        kb,
        kb_context.contextualize_folder(
            kb, model, AnswerCache(tmp_path / "c", model.usage), workers=2
        ),
    )
    chunks = chunk_folder(kb)
    items = [
        kb_validation.Item(
            id=f"q{i}",
            text=chunk.text[:60],
            stratum="local" if i % 2 else "contextual",
            golden=[chunk.chunk_id],
            grades={chunk.chunk_id: 2},
            split="test",
        )
        for i, chunk in enumerate(chunks[:6])
    ]
    report = kb_evaluate.run_evaluation(
        chunks,
        items,
        HASHING,
        {"context": ["context: $.retrieval.context"]},
        ks=(5, 10),
    )
    assert list(report["conditions"]) == [
        "baseline",
        "context:embedding",
        "context:bm25",
        "context:both",
    ]
    hybrid = report["versus_baseline"]["context:both"]["hybrid"]["10"]
    assert set(hybrid) == {"all", "contextual", "local"}
    assert hybrid["all"]["queries"] == 6
    assert "failure_reduction" in hybrid["all"]
    # Tags in the keyword index only leave the vectors as in the baseline.
    base = report["conditions"]["baseline"]["vector"]["k"]["10"]["scores"]
    bm25 = report["conditions"]["context:bm25"]["vector"]["k"]["10"]["scores"]
    assert base == bm25
    assert "failure" in kb_evaluate.summary(report, "10")


def test_kb_evaluate_parse_tag_set():
    assert kb_evaluate.parse_tag_set("ctx=context: $.retrieval.context;p: $.p") == (
        "ctx",
        ["context: $.retrieval.context", "p: $.p"],
    )
    with pytest.raises(ValueError):
        kb_evaluate.parse_tag_set("no-equals")


def test_metrics_ndcg_sign_flip_and_holm():
    assert kb_metrics.ndcg(["a", "b"], {"a": 2, "b": 1}, 2) == 1.0
    assert kb_metrics.ndcg(["x", "a"], {"a": 2}, 2) == pytest.approx(1 / 1.58496, 1e-4)
    assert kb_metrics.sign_flip_p([0.0] * 30, [1.0] * 30) < 0.001
    assert kb_metrics.sign_flip_p([0.5] * 10, [0.5] * 10) == 1.0
    assert kb_metrics.holm({"a": 0.01, "b": 0.04, "c": 0.03}) == {
        "a": True,
        "b": False,
        "c": False,
    }


def test_kb_context_window_keeps_the_opening_and_the_neighbourhood():
    ordered = [f"section {i} " + "x" * 300 for i in range(10)]
    document = "OPENING " + "\n\n".join(ordered)
    text = kb_context.window(document, ordered, 5, 800)
    assert text.startswith("OPENING")
    assert "section 5" in text and "section 9" not in text
    assert len(text) < 900


def test_anthropic_pages_map_every_published_chunk_to_its_section(
    tmp_path, monkeypatch
):
    from benchmarks.tagged_retrieval import kb_datasets
    from benchmarks.tagged_retrieval.datasets import Dataset, Query
    from duckdb_vectors.models import Chunk

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


def test_kb_analyze_applies_holm_and_the_ci_to_the_primary_stratum():
    from benchmarks.tagged_retrieval import kb_analyze

    def report(corpus, p, low, strata=("contextual", "local")):
        entry = {
            "queries": 10,
            "baseline_failure": 0.4,
            "failure_reduction": 0.25,
            "pass": {"difference": 0.1, "ci95": [low, 0.2], "p": p},
        }
        groups = {"all": entry, **{s: entry for s in strata}}
        return {
            "corpus": corpus,
            "backend": "hashing",
            "strata": list(strata),
            "versus_baseline": {"context:both": {"hybrid": {"20": groups}}},
        }

    reports = [
        report("a", 0.001, 0.05),
        report("b", 0.06, 0.01),
        report("c", 0.001, -0.01),
    ]
    assert kb_analyze.decisions(reports) == {
        "a / hashing": True,
        "b / hashing": False,  # p above 0.05 fails Holm
        "c / hashing": False,  # the interval crosses zero
    }
    assert "contextual" in kb_analyze.table(reports)


def test_batch_mode_queues_then_completes_from_fetched_answers(kb, tmp_path):
    from scripts import kb_batch
    from scripts.kb_common import BatchQueue, request_key

    queue = BatchQueue(tmp_path / "batch")
    model = Model("fake", batch=queue)
    cache = AnswerCache(tmp_path / "cache", model.usage)
    contexts = kb_context.contextualize_folder(kb, model, cache, workers=2)
    assert all(not page for page in contexts.values())  # nothing answered yet
    waiting = queue.save()
    assert waiting == queue.deferred > 0
    pending = [
        json.loads(line)
        for line in (tmp_path / "batch" / "pending.jsonl").read_text().splitlines()
    ]
    rows = []
    for request in pending:
        batch_record = kb_batch.record(request)
        assert batch_record["modelInput"]["messages"][0]["role"] == "user"
        text = fake_invoke(
            request["system"], [{"text": t} for t in request["texts"]], 300
        )
        rows.append(
            json.dumps(
                {
                    "recordId": request["key"],
                    "modelOutput": {
                        "content": [{"type": "text", "text": text}],
                        "usage": {"input_tokens": 10, "output_tokens": 2},
                    },
                }
            )
        )
    rows.append(json.dumps({"recordId": "x", "error": {"errorMessage": "boom"}}))
    answers, errors = kb_batch.parse_output("\n".join(rows))
    assert len(answers) == waiting and len(errors) == 1
    with (tmp_path / "batch" / "answers.jsonl").open("w") as handle:
        for answer in answers:
            handle.write(json.dumps({"key": answer["key"], "text": answer["text"]}))
            handle.write("\n")
    replay = Model("fake", batch=BatchQueue(tmp_path / "batch"))
    done = kb_context.contextualize_folder(kb, replay, cache, workers=2)
    assert replay.batch.deferred == 0
    assert all(page for page in done.values())
    assert request_key("m", "s", [{"text": "a"}, {"cachePoint": {}}], 5) == (
        request_key("m", "s", [{"text": "a"}], 5)
    )


def test_batch_jobs_respect_the_record_limit(monkeypatch):
    from scripts import kb_batch

    monkeypatch.setattr(kb_batch, "MAX_RECORDS", 3)
    assert [len(job) for job in kb_batch.split_jobs(["{}"] * 7)] == [3, 3, 1]


def test_curation_chain_runs_offline_with_the_fake_model(kb, tmp_path):
    """What ``make kb-validation``, ``kb-context``, ``kb-tags`` and
    ``kb-evaluate`` run, with ``--model fake``: no AWS, no cost."""
    from benchmarks.tagged_retrieval import kb_analyze

    work = str(tmp_path / "work")
    fake = ["--model", "fake"]
    common = [str(kb), "--work", work]
    steps = [
        (kb_validation, ["local", *common, *fake, "--size", "20", "--min-words", "10"]),
        (
            kb_validation,
            ["contextual", *common, *fake, "--size", "10", "--min-words", "10"]
            + ["--backends", HASHING],
        ),
        (kb_validation, ["split", *common]),
        (kb_validation, ["pool", *common, "--backends", HASHING]),
        (kb_validation, ["judge", *common, *fake]),
        (kb_validation, ["audit", *common, "--size", "6"]),
        (kb_context, [*common, *fake]),
        (kb_tags, ["discover", *common, *fake]),
        (kb_tags, ["tag", *common, *fake]),
        (kb_tags, ["apply", *common]),
    ]
    for module, argv in steps:
        assert module.main(argv) == 0, argv[0]
    validation = json.loads((tmp_path / "work" / "validation.json").read_text())
    strata = {item["stratum"] for item in validation["items"]}
    assert strata == {"local", "contextual"}
    assert all(item["golden"] for item in validation["items"] if item["valid"])
    assert (tmp_path / "work" / "audit.yaml").exists()
    front = read_front_matter((kb / "refund-policy.md").read_text())
    assert front["retrieval"]["subject"] and front["sections"]
    output = tmp_path / "work" / "results" / "all.json"
    argv = [*common, "--split", "all", "--backend", HASHING, "--output", str(output)]
    argv += ["--tags", "context=context: $.retrieval.context"]
    assert kb_evaluate.main(argv) == 0
    assert kb_analyze.main([str(output)]) == 0


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


def test_kb_tags_apply_moves_legacy_fields_and_keeps_the_rest(kb, caplog):
    page = kb / "deploy-runbook.md"
    text = page.read_text(encoding="utf-8")
    text = set_front_matter_key(text, "llm", {"product": "Beacon", "mine": "x"})
    page.write_text(text, encoding="utf-8")
    assert "llm" not in kb_tags.existing_fields(kb_tags.pages(kb))
    assert "legacy 'llm' key" in caplog.text
    kb_tags.apply_tags(kb, {"deploy-runbook.md": {"product": "Cedar"}}, ["product"])
    front = read_front_matter(page.read_text(encoding="utf-8"))
    assert front["retrieval"] == {"product": "Cedar"}
    assert front["llm"] == {"mine": "x"}
    kb_tags.apply_tags(kb, {"deploy-runbook.md": {"mine": "y"}}, ["mine"])
    front = read_front_matter(page.read_text(encoding="utf-8"))
    assert "llm" not in front
    assert front["retrieval"] == {"product": "Cedar", "mine": "y"}

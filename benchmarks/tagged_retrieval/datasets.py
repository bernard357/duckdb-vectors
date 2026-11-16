"""Dataset loaders: a corpus of chunks with native tags, and queries with
golden chunks.

The Anthropic cookbook set is used chunk for chunk, as published, so a baseline
here is comparable with the cookbook's own Pass@k. Its corpus file carries no
metadata; the repository and path of each file exist only in the evaluation
file, in ``golden_documents[].meta``, and are joined back on the document uuid.
"""

import hashlib
import json
import re
import urllib.request
from pathlib import Path, PurePosixPath

from pydantic import BaseModel

from duckdb_vectors.chunkers.windowing import split_windows
from duckdb_vectors.models import Chunk

# Pinned so a run names the exact data it used (claude-cookbooks, MIT licence).
COOKBOOK_COMMIT = "ce8b2629efe8b24048b127b4c79af1e9cbab93d4"
_BASE = (
    "https://raw.githubusercontent.com/anthropics/claude-cookbooks/"
    f"{COOKBOOK_COMMIT}/capabilities/contextual-embeddings/data/"
)
CHUNKS_FILE = "codebase_chunks.json"
EVALUATION_FILE = "evaluation_set.jsonl"


class Query(BaseModel):
    """A question and the chunk ids that answer it."""

    text: str
    golden: list[str]
    alternatives: list[list[str]] = []


class Dataset(BaseModel):
    """A corpus, its queries and where it came from."""

    name: str
    chunks: list[Chunk]
    queries: list[Query]
    sources: dict[str, str]
    quality: dict[str, int] = {}
    default_tag_fields: list[str] = []


def _chunk_id(document_uuid: str, index: int) -> str:
    return f"{document_uuid}:{index}"


def parse_anthropic(
    chunks_json: str, evaluation_jsonl: str, sources: dict[str, str] | None = None
) -> Dataset:
    """Build the dataset from the two cookbook files' text.

    Every chunk carries its document's native tags as metadata: ``repo_name``,
    ``relative_path``, ``doctype``, ``source`` and ``num_chunks`` as published,
    plus ``directory`` and ``extension`` derived from the path. Raises
    ``ValueError`` if a document has no metadata in the evaluation file or a
    golden chunk is missing from the corpus.
    """
    documents = json.loads(chunks_json)
    rows = [json.loads(line) for line in evaluation_jsonl.splitlines() if line.strip()]
    meta: dict[str, dict] = {}
    for row in rows:
        for document in row["golden_documents"]:
            meta[document["uuid"]] = document["meta"]
    chunks: list[Chunk] = []
    for document in documents:
        uuid = document["original_uuid"]
        if uuid not in meta:
            raise ValueError(f"document {uuid} has no metadata in the evaluation set")
        native = meta[uuid]
        path = PurePosixPath(native["relative_path"])
        tags = {
            "repo_name": native["repo_name"],
            "relative_path": native["relative_path"],
            "directory": str(path.parent),
            "extension": path.suffix.lstrip("."),
            "doctype": native["doctype"],
            "source": native["source"],
            "num_chunks": native["num_chunks"],
        }
        for chunk in document["chunks"]:
            index = chunk["original_index"]
            chunks.append(
                Chunk(
                    chunk_id=_chunk_id(uuid, index),
                    text=chunk["content"],
                    source=native["relative_path"],
                    position=index,
                    title=path.name,
                    metadata=tags,
                )
            )
    known = {chunk.chunk_id for chunk in chunks}
    queries = []
    for row in rows:
        golden = [_chunk_id(uuid, index) for uuid, index in row["golden_chunk_uuids"]]
        missing = [chunk_id for chunk_id in golden if chunk_id not in known]
        if missing:
            raise ValueError(f"golden chunks missing from the corpus: {missing[:3]}")
        queries.append(Query(text=row["query"], golden=golden))
    return Dataset(
        name="anthropic-codebases",
        chunks=chunks,
        queries=queries,
        sources=sources or {},
        default_tag_fields=[
            "repo: $.repo_name",
            "directory: $.directory",
            "extension: $.extension",
        ],
    )


def _fetch(name: str, cache: Path) -> Path:
    target = cache / name
    if not target.exists():
        cache.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(_BASE + name, timeout=60) as response:
            target.write_bytes(response.read())
    return target


def load_anthropic(cache: Path) -> Dataset:
    """Download (once) and parse the Anthropic cookbook codebase set."""
    paths = {name: _fetch(name, cache) for name in (CHUNKS_FILE, EVALUATION_FILE)}
    digests = {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in paths.items()
    }
    return parse_anthropic(
        paths[CHUNKS_FILE].read_text(encoding="utf-8"),
        paths[EVALUATION_FILE].read_text(encoding="utf-8"),
        sources={
            "commit": COOKBOOK_COMMIT,
            **{f"sha256:{k}": v for k, v in digests.items()},
        },
    )


# RAGMATE-10K (Yousuf et al., ECIR 2026): 25 SEC 10-K filings and questions whose
# ground truth is the chunks cited by a RAG pipeline's answer. The repository
# states no licence, so the data is used for local benchmarking and the derived
# set is not republished.
RAGMATE_COMMIT = "07664f4507c4c72270842157f03285f333a477fa"
_RAGMATE = (
    f"https://raw.githubusercontent.com/raquibvt/RAGMate/{RAGMATE_COMMIT}/dataset/"
)
RAGMATE_TICKERS = ("AAPL", "ADBE", "GOOGL", "NVDA", "ORCL")
RAGMATE_YEARS = (2020, 2021, 2022, 2023, 2024)
RAGMATE_CHUNK_TOKENS = 350
RAGMATE_OVERLAP_TOKENS = 50
_RAGMATE_FIELDS = {
    "company_name": "company_name",
    "form_type": "form_type",
    "period_of_report": "period_of_report",
    "filed_date": "filed_date",
    "standard_industrial_classification": "industry",
}


def _ragmate_chunks(forms: dict[str, list[dict]]) -> list[Chunk]:
    """Re-chunk the filings exactly as the dataset's own script does.

    Sections are split into windows of 350 ``cl100k_base`` tokens with an
    overlap of 50, and each chunk is named ``{file}_sec_{sha1(section)[:8]}_
    chunk_{n}``, which is how the ground truth cites them.
    """
    try:
        import tiktoken
    except ImportError as error:  # pragma: no cover - optional dependency
        raise ImportError(
            "The RAGMATE loader needs tiktoken: "
            "pip install 'duckdb-vectors[benchmarks]'"
        ) from error
    encoder = tiktoken.get_encoding("cl100k_base")
    step = RAGMATE_CHUNK_TOKENS - RAGMATE_OVERLAP_TOKENS
    chunks: list[Chunk] = []
    for filename, sections in forms.items():
        ticker, year = filename.split("_")[:2]
        for section in sections:
            meta = section.get("metadata", {})
            document = meta.get("metadata", {})
            title = meta.get("section", "no_section")
            digest = hashlib.sha1(title.encode("utf-8")).hexdigest()[:8]
            tags: dict = {
                label: document[key]
                for key, label in _RAGMATE_FIELDS.items()
                if document.get(key) not in (None, "", [])
            }
            tags.update(
                ticker=ticker,
                fiscal_year=str(document.get("period_of_report", year))[:4],
                section=title,
            )
            tokens = encoder.encode(section["text"])
            for number, start in enumerate(range(0, len(tokens), step)):
                piece = encoder.decode(tokens[start : start + RAGMATE_CHUNK_TOKENS])
                pieces = encoder.encode(piece)
                if len(pieces) > 512:
                    piece = encoder.decode(pieces[:512])
                chunks.append(
                    Chunk(
                        chunk_id=f"{filename}_sec_{digest}_chunk_{number}",
                        text=piece,
                        source=filename,
                        position=number,
                        title=title,
                        metadata=tags,
                    )
                )
    return chunks


def _repair_citation(cited: str) -> str:
    """Strip the ``chunk_id:`` label some citations carry."""
    return cited.split(":", 1)[1].strip() if cited.startswith("chunk_id:") else cited


def parse_ragmate(
    forms: dict[str, list[dict]],
    ground_truth: list[dict],
    sources: dict[str, str] | None = None,
) -> Dataset:
    """Build the dataset and its quality report from the dataset's files.

    A question keeps the cited chunk ids that exist in the re-chunked corpus,
    after repairing a ``chunk_id:`` label; questions left with none are dropped.
    The quality report counts what was repaired or dropped and checks the cited
    chunk texts shipped with the ground truth against the re-chunked text.
    """
    chunks = _ragmate_chunks(forms)
    known = {chunk.chunk_id: chunk.text for chunk in chunks}
    quality = {
        "questions": len(ground_truth),
        "cited": 0,
        "cited_repaired": 0,
        "cited_dropped": 0,
        "texts_checked": 0,
        "texts_identical": 0,
    }
    queries = []
    for row in ground_truth:
        golden = []
        for cited in row["cited_chunk_ids"]:
            quality["cited"] += 1
            fixed = _repair_citation(cited)
            quality["cited_repaired"] += fixed != cited
            if fixed in known:
                golden.append(fixed)
            else:
                quality["cited_dropped"] += 1
        for item in row["cited_chunks"]:
            if item["id"] in known:
                quality["texts_checked"] += 1
                quality["texts_identical"] += item["text"] == known[item["id"]]
        if golden:
            queries.append(
                Query(text=row["question"], golden=list(dict.fromkeys(golden)))
            )
    quality["questions_used"] = len(queries)
    return Dataset(
        name="ragmate-10k",
        chunks=chunks,
        queries=queries,
        sources=sources or {},
        quality=quality,
        default_tag_fields=[
            "company: $.company_name",
            "year: $.fiscal_year",
            "section: $.section",
        ],
    )


def load_ragmate(cache: Path) -> Dataset:
    """Download (once) and parse RAGMATE-10K."""
    names = [
        f"{ticker}_{year}_10-K_chunks.json"
        for ticker in RAGMATE_TICKERS
        for year in RAGMATE_YEARS
    ]
    paths = {}
    for name in names:
        target = cache / "ragmate" / name
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(
                _RAGMATE + "10K_forms/" + name, timeout=60
            ) as r:
                target.write_bytes(r.read())
        paths[name] = target
    truth = cache / "ragmate" / "ground_truth.json"
    if not truth.exists():
        with urllib.request.urlopen(_RAGMATE + "ground_truth.json", timeout=120) as r:
            truth.write_bytes(r.read())
    digests = {
        name: hashlib.sha256(path.read_bytes()).hexdigest()
        for name, path in {**paths, "ground_truth.json": truth}.items()
    }
    forms = {name: json.loads(path.read_text("utf-8")) for name, path in paths.items()}
    return parse_ragmate(
        forms,
        json.loads(truth.read_text("utf-8")),
        sources={
            "commit": RAGMATE_COMMIT,
            "sha256": hashlib.sha256(
                "".join(sorted(digests.values())).encode()
            ).hexdigest(),
        },
    )


# MultiHop-RAG (Tang and Yang, 2024): news articles and multi-hop questions whose
# evidence is a quoted fact in two to four articles. Licensed ODC-BY.
_MULTIHOP = "https://huggingface.co/datasets/yixuantt/MultiHopRAG/resolve/main/"
MULTIHOP_TARGET_WORDS = 300
MULTIHOP_OVERLAP_WORDS = 40
_SPACES = re.compile(r"\s+")


def _squash(text: str) -> str:
    return _SPACES.sub(" ", text).strip()


def parse_multihop(
    corpus: list[dict], questions: list[dict], sources: dict[str, str] | None = None
) -> Dataset:
    """Chunk the articles and map each quoted fact to the chunks that hold it.

    Articles are cut into the package's own overlapping word windows. A fact
    counts as found in every chunk that contains it, and a query is satisfied
    for that fact by any one of them. Facts that no chunk contains are dropped,
    and queries left without evidence, and queries that have none at all
    (``null_query``), are not used. The quality report counts each case.
    """
    chunks: list[Chunk] = []
    by_url: dict[str, list[tuple[str, str]]] = {}
    for article in corpus:
        tags = {
            key: article[key]
            for key in ("title", "author", "source", "category", "published_at")
            if article.get(key) not in (None, "", "None")
        }
        windows = split_windows(
            article["body"], MULTIHOP_TARGET_WORDS, MULTIHOP_OVERLAP_WORDS
        )
        by_url[article["url"]] = []
        for number, window in enumerate(windows):
            chunk_id = f"{article['url']}#{number}"
            by_url[article["url"]].append((chunk_id, _squash(window)))
            chunks.append(
                Chunk(
                    chunk_id=chunk_id,
                    text=window,
                    source=article["url"],
                    position=number,
                    title=article["title"],
                    metadata=tags,
                )
            )
    quality = {
        "questions": len(questions),
        "null_queries": 0,
        "evidence": 0,
        "evidence_dropped": 0,
        "evidence_in_several_chunks": 0,
    }
    queries = []
    for row in questions:
        if row["question_type"] == "null_query" or not row["evidence_list"]:
            quality["null_queries"] += 1
            continue
        alternatives = []
        for evidence in row["evidence_list"]:
            quality["evidence"] += 1
            fact = _squash(evidence["fact"])
            holders = [
                chunk_id
                for chunk_id, text in by_url.get(evidence["url"], [])
                if fact in text
            ]
            if not holders:
                quality["evidence_dropped"] += 1
                continue
            quality["evidence_in_several_chunks"] += len(holders) > 1
            alternatives.append(holders)
        if alternatives:
            golden = list(dict.fromkeys(c for group in alternatives for c in group))
            queries.append(
                Query(text=row["query"], golden=golden, alternatives=alternatives)
            )
    quality["questions_used"] = len(queries)
    return Dataset(
        name="multihop-rag",
        chunks=chunks,
        queries=queries,
        sources=sources or {},
        quality=quality,
        default_tag_fields=[
            "title: $.title",
            "source: $.source",
            "category: $.category",
            "published: $.published_at",
        ],
    )


def load_multihop(cache: Path) -> Dataset:
    """Download (once) and parse MultiHop-RAG."""
    paths = {}
    for name in ("corpus.json", "MultiHopRAG.json"):
        target = cache / "multihop" / name
        if not target.exists():
            target.parent.mkdir(parents=True, exist_ok=True)
            with urllib.request.urlopen(_MULTIHOP + name, timeout=120) as response:
                target.write_bytes(response.read())
        paths[name] = target
    return parse_multihop(
        json.loads(paths["corpus.json"].read_text("utf-8")),
        json.loads(paths["MultiHopRAG.json"].read_text("utf-8")),
        sources={
            f"sha256:{name}": hashlib.sha256(path.read_bytes()).hexdigest()
            for name, path in paths.items()
        },
    )


_BRAIN_FIELDS = (
    "aws_service",
    "document_type",
    "programming_language",
    "operational_domain",
    "audience_level",
    "has_code_examples",
)


def parse_engineering_brain(
    chunks: list[Chunk],
    derived: dict[str, dict[str, str | None]],
    generated: dict,
    sources: dict[str, str] | None = None,
) -> Dataset:
    """Combine the corpus chunks, the model-derived tags and generated questions.

    Each page's derived tags replace its ``llm`` front matter, so the benchmark
    can test every proposed tag, including the ones left out of the pages. The
    golden chunk of a question is the chunk it was written from.
    """
    known = {chunk.chunk_id for chunk in chunks}
    for chunk in chunks:
        values = derived.get(chunk.source, {})
        llm = {name: values[name] for name in _BRAIN_FIELDS if values.get(name)}
        chunk.metadata = {**chunk.metadata, "llm": llm}
    queries = [
        Query(text=item["question"], golden=[item["chunk_id"]])
        for item in generated["queries"]
        if item["chunk_id"] in known
    ]
    quality = {**generated["quality"], "questions_used": len(queries)}
    return Dataset(
        name="engineering-brain",
        chunks=chunks,
        queries=queries,
        sources=sources or {},
        quality=quality,
        default_tag_fields=[
            "topic: $.topic",
            *(f"{name}: $.llm.{name}" for name in _BRAIN_FIELDS),
        ],
    )


def load_engineering_brain(cache: Path) -> Dataset:
    """Load the private engineering-brain set.

    Needs ``ENGINEERING_BRAIN_DIR`` (the documentation folder) and the work
    folder ``ENGINEERING_BRAIN_WORK`` (default ``tmp/enrichment``) holding
    ``tags.json`` from ``enrich`` and ``queries.json`` from ``queries``. The
    corpus and the questions are private and are not published.
    """
    import os

    from benchmarks.tagged_retrieval.queries import chunk_folder

    folder = os.environ.get("ENGINEERING_BRAIN_DIR")
    if not folder:
        raise ValueError("set ENGINEERING_BRAIN_DIR to the documentation folder")
    work = Path(os.environ.get("ENGINEERING_BRAIN_WORK", "tmp/enrichment"))
    tags = json.loads((work / "tags.json").read_text(encoding="utf-8"))
    generated = json.loads((work / "queries.json").read_text(encoding="utf-8"))
    return parse_engineering_brain(
        chunk_folder(Path(folder)),
        tags,
        generated,
        sources={
            "prompt_version": str(generated["prompt_version"]),
            "model": generated["model"],
        },
    )


LOADERS = {
    "anthropic": load_anthropic,
    "ragmate": load_ragmate,
    "multihop": load_multihop,
    "engineering-brain": load_engineering_brain,
}

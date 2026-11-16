"""Corpus statistics that tell effective tags from decorative ones.

A knowledge manager declares tags (see :mod:`duckdb_vectors.tagging`); this
module measures, for each tag, how it is spread over the documents of an index
and how much it separates the chunks of one document from the chunks of other
documents once embedded. It answers "which tags can help", not "which tags help
for my questions": query-based ablation belongs to the benchmark harness.

Per tag the report gives:

* ``coverage``: share of documents that carry the tag.
* ``cardinality``: number of distinct values among those documents. A list is
  one value (its comma-joined form).
* ``entropy``: Shannon entropy of the value distribution divided by its maximum,
  from 0 (one value everywhere) to 1 (all values equally common).
* ``documents_per_value``: mean number of documents sharing a value.
* ``margin_*`` and ``d_*``: the gap between the mean cosine similarity of chunk
  pairs from the same document and of pairs from different documents, and the
  same gap in pooled standard deviations (Cohen's d), measured on embeddings of
  the raw chunks (``*_without``) and of chunks prefixed with this tag alone
  (``*_with``); ``*_lift`` is the difference.

The verdict thresholds below are provisional. They are set from first
principles, not from benchmark results, and will be recalibrated.
"""

import math
import random
from collections import Counter

import duckdb
from pydantic import BaseModel

from duckdb_vectors.models import Chunk
from duckdb_vectors.store import VectorStore
from duckdb_vectors.tagging import (
    TagField,
    parse_tag_fields,
    project_many,
    tagged_text,
)

# Provisional verdict thresholds (see the module docstring).
MIN_COVERAGE = 0.5
CONSTANT_ENTROPY = 0.05
IDENTIFIER_ENTROPY = 0.95
MIN_LIFT_D = 0.1

# Pairs sampled per group when measuring separation, drawn with a fixed seed.
MAX_PAIRS = 20_000
_SEED = 0
_CHARS_PER_TOKEN = 4

_WINDOWS = {"fastembed": 512, "hf": 512, "bedrock": 8192}


class TagStat(BaseModel):
    """Statistics and verdict for one tag."""

    label: str
    path: str
    documents: int
    coverage: float
    cardinality: int
    entropy: float
    documents_per_value: float
    margin_without: float | None = None
    margin_with: float | None = None
    margin_lift: float | None = None
    d_without: float | None = None
    d_with: float | None = None
    d_lift: float | None = None
    verdict: str
    reasons: list[str]


class TagsReport(BaseModel):
    """The ``tags`` report: one :class:`TagStat` per tag, plus warnings."""

    documents: int
    chunks: int
    tags: list[TagStat]
    warnings: list[str]


def input_window(backend: str) -> int | None:
    """Return the model's input window in tokens, or ``None`` when unlimited
    or unknown.

    Under fastembed 0.9.1 every model truncates at 512 tokens, the static
    default included; the ``hf`` loader defaults to 512; Titan v2 accepts
    8,192 tokens. The hashing backend has no limit, and an unrecognised
    backend gives no warning rather than a guess.
    """
    return _WINDOWS.get(backend.partition(":")[0])


def _document_values(
    chunks: list[Chunk], fields: list[TagField]
) -> tuple[list[str], list[list[tuple[str, str]]]]:
    """Return each chunk's document id and its projected tags."""
    tags = project_many([chunk.metadata for chunk in chunks], fields)
    return [chunk.source or chunk.chunk_id for chunk in chunks], tags


def _per_document(
    documents: list[str], tags: list[list[tuple[str, str]]], label: str
) -> dict[str, str]:
    """First non-empty value of ``label`` per document, in chunk order."""
    values: dict[str, str] = {}
    for document, chunk_tags in zip(documents, tags, strict=True):
        if document in values:
            continue
        for tag_label, value in chunk_tags:
            if tag_label == label:
                values[document] = value
                break
    return values


def _entropy(counts: Counter) -> float:
    """Normalised Shannon entropy of a value count distribution."""
    total = sum(counts.values())
    if len(counts) <= 1 or total == 0:
        return 0.0
    entropy = -sum(c / total * math.log(c / total) for c in counts.values())
    return entropy / math.log(len(counts))


def _pairs(documents: list[str]) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Sample same-document and cross-document chunk index pairs."""
    rng = random.Random(_SEED)
    by_document: dict[str, list[int]] = {}
    for index, document in enumerate(documents):
        by_document.setdefault(document, []).append(index)
    same = [
        (a, b)
        for members in by_document.values()
        for position, a in enumerate(members)
        for b in members[position + 1 :]
    ]
    if len(same) > MAX_PAIRS:
        same = rng.sample(same, MAX_PAIRS)
    cross: list[tuple[int, int]] = []
    if len(by_document) > 1:
        count = len(documents)
        target = min(MAX_PAIRS, max(len(same), 1))
        attempts = 0
        while len(cross) < target and attempts < MAX_PAIRS * 20:
            attempts += 1
            a, b = rng.randrange(count), rng.randrange(count)
            if documents[a] != documents[b]:
                cross.append((a, b))
    return same, cross


def _separation(
    vectors: list[list[float]],
    same: list[tuple[int, int]],
    cross: list[tuple[int, int]],
) -> tuple[float, float] | None:
    """Return ``(margin, cohen_d)`` or ``None`` when a group has too few pairs."""
    if len(same) < 2 or len(cross) < 2:
        return None
    con = duckdb.connect()
    try:
        con.execute("CREATE TABLE v (i INTEGER, e FLOAT[])")
        con.execute(
            "INSERT INTO v SELECT unnest($i), unnest($e)",
            {"i": list(range(len(vectors))), "e": vectors},
        )
        stats: list[tuple[float, float, int]] = []
        for group in (same, cross):
            con.execute("CREATE OR REPLACE TABLE p (a INTEGER, b INTEGER)")
            con.execute(
                "INSERT INTO p SELECT unnest($a), unnest($b)",
                {"a": [a for a, _ in group], "b": [b for _, b in group]},
            )
            row = con.execute(
                "SELECT avg(s), var_samp(s), count(*) FROM ("
                "SELECT list_cosine_similarity(x.e, y.e) AS s "
                "FROM p JOIN v x ON x.i = p.a JOIN v y ON y.i = p.b)"
            ).fetchone()
            if row is None:
                return None
            stats.append((row[0], row[1], row[2]))
    finally:
        con.close()
    (mean_same, var_same, n_same), (mean_cross, var_cross, n_cross) = stats
    margin = mean_same - mean_cross
    pooled = ((n_same - 1) * var_same + (n_cross - 1) * var_cross) / (
        n_same + n_cross - 2
    )
    return margin, (margin / math.sqrt(pooled) if pooled > 0 else 0.0)


def _verdict(stat: TagStat) -> tuple[str, list[str]]:
    """Classify a tag as ``effective`` or ``decorative`` with the reasons."""
    reasons = []
    if stat.coverage < MIN_COVERAGE:
        reasons.append(
            f"sparse: coverage {stat.coverage:.0%} is below {MIN_COVERAGE:.0%}"
        )
    if stat.documents and stat.entropy <= CONSTANT_ENTROPY:
        reasons.append("constant: one value for every document that has the tag")
    if (
        stat.entropy >= IDENTIFIER_ENTROPY
        and stat.documents_per_value <= 1.0
        and stat.documents > 2
    ):
        reasons.append("identifier: each value belongs to a single document")
    if stat.d_lift is not None and stat.d_lift < MIN_LIFT_D:
        reasons.append(
            f"negligible separation: Cohen's d lift {stat.d_lift:.2f} "
            f"is below {MIN_LIFT_D}"
        )
    return ("decorative" if reasons else "effective"), reasons


def _overflow_warning(
    store: VectorStore, chunks: list[Chunk], fields: list[TagField]
) -> list[str]:
    """Warn when prefix plus chunk probably exceeds the model's input window."""
    window = input_window(store.settings.backend)
    if window is None or not chunks:
        return []
    tags = project_many([chunk.metadata for chunk in chunks], fields)
    over = sum(
        1
        for chunk, chunk_tags in zip(chunks, tags, strict=True)
        if len(tagged_text(chunk.text, chunk_tags)) / _CHARS_PER_TOKEN > window
    )
    if not over:
        return []
    return [
        f"{over} of {len(chunks)} chunks probably exceed the {window}-token input "
        f"window of {store.settings.backend} once tagged (estimated at "
        f"{_CHARS_PER_TOKEN} characters per token); the model cuts the end of "
        "the text, not the prefix."
    ]


def compute_tag_report(
    store: VectorStore, fields: list[str] | None = None
) -> TagsReport:
    """Measure every tag over the chunks of ``store``.

    ``fields`` defaults to ``store.settings.tag_fields``. Embeds the corpus once
    without tags and once per tag with that tag alone, using the store's own
    embedder. Raises ``ValueError`` when no tag is declared.
    """
    parsed = parse_tag_fields(store.settings.tag_fields if fields is None else fields)
    if not parsed:
        raise ValueError(
            "No tags declared: pass --tag-fields or set DUCKDB_VECTORS_TAG_FIELDS."
        )
    chunks = store.list_vectors()
    documents, tags = _document_values(chunks, parsed)
    unique_documents = list(dict.fromkeys(documents))
    same, cross = _pairs(documents)
    baseline = store.embedder.embed([chunk.text for chunk in chunks]) if chunks else []
    without = _separation(baseline, same, cross) if chunks else None
    stats = []
    for field in parsed:
        values = _per_document(documents, tags, field.label)
        counts = Counter(values.values())
        covered = len(values)
        total = len(unique_documents)
        stat = TagStat(
            label=field.label,
            path=field.path,
            documents=covered,
            coverage=covered / total if total else 0.0,
            cardinality=len(counts),
            entropy=_entropy(counts),
            documents_per_value=covered / len(counts) if counts else 0.0,
            verdict="effective",
            reasons=[],
        )
        if without is not None and covered:
            single = project_many([chunk.metadata for chunk in chunks], [field])
            texts = [
                tagged_text(chunk.text, chunk_tags)
                for chunk, chunk_tags in zip(chunks, single, strict=True)
            ]
            with_tag = _separation(store.embedder.embed(texts), same, cross)
            if with_tag is not None:
                stat.margin_without, stat.d_without = without
                stat.margin_with, stat.d_with = with_tag
                stat.margin_lift = with_tag[0] - without[0]
                stat.d_lift = with_tag[1] - without[1]
        stat.verdict, stat.reasons = _verdict(stat)
        stats.append(stat)
    return TagsReport(
        documents=len(unique_documents),
        chunks=len(chunks),
        tags=stats,
        warnings=_overflow_warning(store, chunks, parsed),
    )

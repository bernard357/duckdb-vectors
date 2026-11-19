"""Statistics that screen out tags unlikely to help search.

A knowledge manager declares tags (see :mod:`duckdb_vectors.tagging`); this
module measures how each is spread over the documents of an index and, when
sample queries are given, whether queries ever use its values.

Per tag the report gives:

* ``coverage``: share of documents that carry the tag.
* ``cardinality``: number of distinct values among those documents. A list is
  one value (its comma-joined form).
* ``entropy``: Shannon entropy of the value distribution divided by its maximum,
  from 0 (one value everywhere) to 1 (all values equally common).
* ``documents_per_value``: mean number of documents sharing a value. Values
  shared by about one document each get a note, not a verdict: a descriptive
  per-page label can help, an opaque code cannot, and statistics cannot tell.
* ``query_mention`` (with ``queries``): share of the queries that contain at
  least one value of the tag, as whole words.
* ``margin_*``, ``d_*``, ``value_*``: how much the tag raises the similarity of
  chunks of the same document (``margin_*``, ``d_*``) or of chunks that share
  its value (``value_*``) over chunks that do not, as a mean cosine margin and
  in pooled standard deviations (Cohen's d), between embeddings of the raw
  chunks and of chunks prefixed with this tag alone.

The verdict uses coverage, entropy and, when queries are given, ``query_mention``.
The separation measures are reported but do not decide: over 17 tags in four
corpora, their rank correlation with the gain each tag brings in a
leave-one-tag-out run was about zero (-0.03 and -0.12), and they rated the tags
that helped most, a company name and a news source, as decorative. The share of
queries that mention a tag's values did predict the gain (rank correlation 0.62).
The thresholds are fitted to those same 17 tags, so treat them as provisional
until other corpora confirm them.
"""

import math
import random
import re
from collections import Counter
from collections.abc import Sequence

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
NEAR_UNIQUE_DOCUMENTS_PER_VALUE = 1.5
MIN_QUERY_MENTION = 0.2

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
    value_margin_lift: float | None = None
    value_d_lift: float | None = None
    query_mention: float | None = None
    verdict: str
    reasons: list[str]
    notes: list[str] = []


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


def _pairs(
    labels: Sequence[str | None],
) -> tuple[list[tuple[int, int]], list[tuple[int, int]]]:
    """Sample same-label and different-label chunk index pairs.

    A chunk whose label is ``None`` is left out. With document ids as labels the
    pairs are same-document and cross-document; with a tag's values as labels
    they are chunks that share the value and chunks that do not.
    """
    rng = random.Random(_SEED)
    groups: dict[str, list[int]] = {}
    for index, label in enumerate(labels):
        if label is not None:
            groups.setdefault(label, []).append(index)
    members = [indexes for indexes in groups.values() if len(indexes) > 1]
    weights = [len(m) * (len(m) - 1) // 2 for m in members]
    same: list[tuple[int, int]] = []
    if sum(weights) <= MAX_PAIRS:
        same = [
            (a, b)
            for indexes in members
            for position, a in enumerate(indexes)
            for b in indexes[position + 1 :]
        ]
    else:
        for indexes in rng.choices(members, weights=weights, k=MAX_PAIRS):
            first, second = rng.sample(indexes, 2)
            same.append((first, second))
    cross: list[tuple[int, int]] = []
    labelled = [i for i, label in enumerate(labels) if label is not None]
    if len(groups) > 1:
        target = min(MAX_PAIRS, max(len(same), 1))
        attempts = 0
        while len(cross) < target and attempts < MAX_PAIRS * 20:
            attempts += 1
            a, b = rng.choice(labelled), rng.choice(labelled)
            if labels[a] != labels[b]:
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
    if stat.query_mention is not None and stat.query_mention < MIN_QUERY_MENTION:
        reasons.append(
            f"unmentioned: {stat.query_mention:.0%} of the queries contain one of "
            f"its values, below {MIN_QUERY_MENTION:.0%}"
        )
    return ("decorative" if reasons else "effective"), reasons


def _mentions(queries: list[str], values: set[str], is_list: bool = False) -> float:
    """Share of the queries that contain at least one value, as whole words.

    For a tag read from a list (a ``[*]`` path) each item of the comma-joined
    value counts on its own; any other value is matched whole.
    """
    items = {
        item.lower()
        for value in values
        for item in (value.split(", ") if is_list else [value])
        if item
    }
    if not items or not queries:
        return 0.0
    pattern = re.compile(
        r"(?<!\w)("
        + "|".join(map(re.escape, sorted(items, key=len, reverse=True)))
        + r")(?!\w)"
    )
    return sum(1 for query in queries if pattern.search(query.lower())) / len(queries)


def _notes(stat: TagStat) -> list[str]:
    """Cautions that do not change the verdict.

    A tag whose values are nearly unique per document separates documents by
    construction, so its lift can reflect a document fingerprint. That is no
    reason to discard it: a descriptive label can match words in a query, while
    an opaque code cannot. Statistics cannot tell the two apart, so the note
    asks for a check against real queries.
    """
    notes = []
    if (
        stat.documents > 2
        and stat.documents_per_value <= NEAR_UNIQUE_DOCUMENTS_PER_VALUE
    ):
        notes.append(
            "nearly unique: values are shared by about one document each, so the "
            "separation lift may only reflect a document fingerprint; check that "
            "your queries contain such values"
        )
    if stat.query_mention is None:
        notes.append(
            "no queries given: only sparse and constant tags are screened out; "
            "pass queries to see whether they ever use this tag's values"
        )
    return notes


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
    store: VectorStore,
    fields: list[str] | None = None,
    queries: list[str] | None = None,
) -> TagsReport:
    """Measure every tag over the chunks of ``store``.

    ``fields`` defaults to ``store.settings.tag_fields``. Embeds the corpus once
    without tags and once per tag with that tag alone, using the store's own
    embedder. With ``queries``, also measures how often they mention each tag's
    values and decides on that. Raises ``ValueError`` when no tag is declared.
    """
    parsed = parse_tag_fields(store.settings.tag_fields if fields is None else fields)
    if not parsed:
        raise ValueError(
            "No tags declared: pass --tag-field or set DUCKDB_VECTORS_TAG_FIELDS."
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
        if queries:
            stat.query_mention = _mentions(
                queries, set(values.values()), is_list="[*]" in field.path
            )
        if chunks and covered:
            single = project_many([chunk.metadata for chunk in chunks], [field])
            texts = [
                tagged_text(chunk.text, chunk_tags)
                for chunk, chunk_tags in zip(chunks, single, strict=True)
            ]
            vectors = store.embedder.embed(texts)
            if without is not None:
                with_tag = _separation(vectors, same, cross)
                if with_tag is not None:
                    stat.margin_without, stat.d_without = without
                    stat.margin_with, stat.d_with = with_tag
                    stat.margin_lift = with_tag[0] - without[0]
                    stat.d_lift = with_tag[1] - without[1]
            labels = [values.get(document) for document in documents]
            value_same, value_cross = _pairs(labels)
            value_without = _separation(baseline, value_same, value_cross)
            value_with = _separation(vectors, value_same, value_cross)
            if value_without is not None and value_with is not None:
                stat.value_margin_lift = value_with[0] - value_without[0]
                stat.value_d_lift = value_with[1] - value_without[1]
        stat.verdict, stat.reasons = _verdict(stat)
        stat.notes = _notes(stat)
        stats.append(stat)
    return TagsReport(
        documents=len(unique_documents),
        chunks=len(chunks),
        tags=stats,
        warnings=_overflow_warning(store, chunks, parsed),
    )

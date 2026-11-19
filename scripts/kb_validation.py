"""Build a validation set for a Markdown knowledge base: queries, reference
answers and judged golden chunks, split once into dev and test.

The set is built before any tag or context is derived, so tag design cannot leak
into it. Steps, each reading and extending ``WORK/validation.json``:

* ``local``: questions written from one passage, answerable from it alone, with
  a reference answer. A control stratum where page context should not matter.
* ``contextual``: pairs of near-identical passages from different pages are
  mined from a baseline index, and a model writes, from the page of one of them,
  the question that only that one answers, as a user would ask it.
* ``import``: real queries, one per line, from a text file.
* ``split``: freeze a dev/test split by page (about 30% dev), with a digest.
* ``pool``: the top results of several search systems for every query.
* ``judge``: a model grades every pooled passage 0, 1 or 2 against the query
  and its reference answer, never seeing tags, contexts or the system. Golden
  chunks are the ones graded 1 or more.
* ``audit``: a YAML sheet of a balanced sample for a person to grade, blind,
  and ``kappa`` to measure the agreement of that person with the judge.

    $ make kb-validation KB=path/to/pages WORK=tmp/kb/work
    $ .venv/bin/python -m scripts.kb_validation local FOLDER --work DIR

Answers are cached by content; every step records its prompt versions, model
and usage in the file.
"""

import argparse
import datetime
import hashlib
import random
import re
import sys
import tempfile
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel

from development.curation import (
    DEFAULT_MODEL,
    DEFAULT_REGION,
    AnswerCache,
    BatchQueue,
    CachedEmbedder,
    Deferred,
    Model,
    build_index,
    chunk_folder,
    load_prompt,
    parse_json_answer,
    read_json,
    read_sheet,
    split_front_matter,
    write_json,
    write_sheet,
)
from duckdb_vectors.models import Chunk

DEFAULT_BACKENDS = (
    "fastembed:minishlab/potion-multilingual-128M",
    "fastembed:BAAI/bge-small-en-v1.5",
)
MIN_WORDS = 60  # shorter passages make poor questions; --min-words changes it
MAX_COPIED_RUN = 4
OPENING_CHARS = 800
POOL_DEPTH = 20
DEV_SHARE = 0.3
_WORD = re.compile(r"\w+", re.UNICODE)


class Item(BaseModel):
    """One validation query and everything known about its answer."""

    id: str
    text: str
    stratum: str  # local, contextual, real or native
    answer: str = ""
    source: str = ""  # page the question was written from, if any
    origin: list[str] = []  # chunk ids the question was written from
    confuser: str = ""  # chunk id of the look-alike passage (contextual)
    golden: list[str] = []
    alternatives: list[list[str]] = []
    pooled: list[str] = []
    grades: dict[str, int] = {}
    split: str = ""
    valid: bool = True


class ValidationSet(BaseModel):
    """The frozen evaluation set of one knowledge base."""

    corpus: str
    items: list[Item] = []
    steps: list[dict] = []
    split: dict = {}

    def by_id(self) -> dict[str, Item]:
        return {item.id: item for item in self.items}

    def log(self, step: str, **details: object) -> None:
        self.steps.append(
            {
                "step": step,
                "at": datetime.datetime.now(datetime.UTC).isoformat(timespec="seconds"),
                **details,
            }
        )


def load_set(work: Path, corpus: str = "") -> ValidationSet:
    path = work / "validation.json"
    if path.exists():
        return ValidationSet.model_validate(read_json(path))
    return ValidationSet(corpus=corpus)


def save_set(work: Path, validation: ValidationSet) -> None:
    write_json(work / "validation.json", validation.model_dump())


def words(text: str) -> list[str]:
    return [word.lower() for word in _WORD.findall(text)]


def longest_copied_run(question: str, passage: str) -> int:
    """Longest run of consecutive words the question shares with the passage."""
    q, p = words(question), words(passage)
    positions: dict[str, list[int]] = {}
    for index, word in enumerate(p):
        positions.setdefault(word, []).append(index)
    best = 0
    for start in range(len(q)):
        for first in positions.get(q[start], []):
            length = 0
            while (
                start + length < len(q)
                and first + length < len(p)
                and q[start + length] == p[first + length]
            ):
                length += 1
            best = max(best, length)
    return best


def item_id(stratum: str, text: str) -> str:
    return f"{stratum}-{hashlib.sha256(text.encode()).hexdigest()[:10]}"


def eligible(chunks: list[Chunk]) -> list[Chunk]:
    return [c for c in chunks if len(c.text.split()) >= MIN_WORDS]


def opening(folder: Path, source: str) -> str:
    """The first characters of a page's body, to say what the page is about."""
    _, body = split_front_matter((folder / source).read_text(encoding="utf-8"))
    return body.strip()[:OPENING_CHARS]


class Keeper:
    """Drops duplicates and questions that copy their passage, and counts why."""

    def __init__(self, validation: ValidationSet) -> None:
        self.seen = {" ".join(words(item.text)) for item in validation.items}
        self.report: Counter = Counter()

    def accept(self, question: str, passage: str) -> bool:
        self.report["questions"] += 1
        normalized = " ".join(words(question))
        if not normalized:
            self.report["empty"] += 1
            return False
        if normalized in self.seen:
            self.report["duplicates"] += 1
            return False
        if longest_copied_run(question, passage) > MAX_COPIED_RUN:
            self.report["copied_too_much"] += 1
            return False
        self.seen.add(normalized)
        self.report["kept"] += 1
        return True


def generate_local(
    validation: ValidationSet,
    chunks: list[Chunk],
    model: Model,
    cache: AnswerCache,
    size: int,
    per_chunk: int = 2,
    seed: int = 0,
    workers: int = 8,
) -> dict:
    """Add local questions written from a seeded sample of passages."""
    prompt = load_prompt("query-local")
    pool = eligible(chunks)
    sample = random.Random(seed).sample(pool, min(size, len(pool)))

    def one(chunk: Chunk) -> list[dict]:
        key = cache.key(
            prompt.name, prompt.version, model.model_id, per_chunk, chunk.text
        )
        answer = cache.get(key)
        if answer is None:
            raw = model.text(
                prompt.system,
                prompt.render(passage=chunk.text, count=str(per_chunk)),
                800,
            )
            answer = cache.put(key, parse_json_answer(raw).get("items", []))
        return answer

    with ThreadPoolExecutor(workers) as executor:
        answers = list(executor.map(one, sample))
    keeper = Keeper(validation)
    for chunk, items in zip(sample, answers, strict=True):
        for entry in items:
            question = str(entry.get("question") or "").strip()
            if not keeper.accept(question, chunk.text):
                continue
            validation.items.append(
                Item(
                    id=item_id("local", question),
                    text=question,
                    stratum="local",
                    answer=str(entry.get("answer") or ""),
                    source=chunk.source,
                    origin=[chunk.chunk_id],
                )
            )
    report = {"passages": len(sample), **keeper.report}
    validation.log("local", prompt=f"{prompt.name}.v{prompt.version}", **report)
    return report


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    norm = (sum(x * x for x in a) * sum(y * y for y in b)) ** 0.5
    return dot / norm if norm else 0.0


def mine_pairs(
    chunks: list[Chunk],
    backend: str,
    size: int,
    seed: int = 0,
    neighbours: int = 10,
) -> list[tuple[Chunk, Chunk, float]]:
    """The ``size`` most similar (passage, look-alike) pairs across pages.

    Seeds are a sample of twice ``size`` passages; each is searched in a
    baseline hybrid index of the folder, and its best-ranked passage from
    another page is its look-alike. Pairs are ranked by the cosine similarity of
    the two passages and the most similar are kept, one per page of the seed.
    """
    from duckdb_vectors.embedders.registry import resolve_embedder

    pool = eligible(chunks)
    seeds = random.Random(seed).sample(pool, min(2 * size, len(pool)))
    by_id = {chunk.chunk_id: chunk for chunk in chunks}
    embedder = CachedEmbedder(resolve_embedder(backend))
    pairs: list[tuple[Chunk, Chunk, float]] = []
    with tempfile.TemporaryDirectory() as directory:
        store = build_index(
            chunks, Path(directory) / "mine.duckdb", backend, embedder=embedder
        )
        for chunk in seeds:
            for hit in store.query(chunk.text, top_k=neighbours, hybrid=True):
                other = by_id.get(hit.chunk.chunk_id)
                if other is None or other.source == chunk.source:
                    continue
                if len(other.text.split()) < MIN_WORDS:
                    continue
                a, b = embedder.embed([chunk.text, other.text])
                pairs.append((chunk, other, cosine(a, b)))
                break
        store.close()
    pairs.sort(key=lambda pair: -pair[2])
    kept: list[tuple[Chunk, Chunk, float]] = []
    used: set[str] = set()
    for pair in pairs:
        if pair[0].source in used:
            continue
        used.add(pair[0].source)
        kept.append(pair)
        if len(kept) == size:
            break
    if len(kept) < size:  # fewer pages than wanted: allow a page twice
        for pair in pairs:
            if pair not in kept:
                kept.append(pair)
            if len(kept) == size:
                break
    return kept


def generate_contextual(
    validation: ValidationSet,
    folder: Path,
    chunks: list[Chunk],
    model: Model,
    cache: AnswerCache,
    size: int,
    backend: str = DEFAULT_BACKENDS[0],
    seed: int = 0,
    workers: int = 8,
) -> dict:
    """Add context-dependent questions written from mined look-alike pairs."""
    prompt = load_prompt("query-contextual")
    pairs = mine_pairs(chunks, backend, size, seed)
    openings: dict[str, str] = {}

    def page_opening(source: str) -> str:
        if source not in openings:
            openings[source] = opening(folder, source)
        return openings[source]

    for chunk, other, _ in pairs:
        page_opening(chunk.source)
        page_opening(other.source)

    def one(pair: tuple[Chunk, Chunk, float]) -> dict:
        chunk, other, _ = pair
        values = {
            "page": openings[chunk.source],
            "passage": chunk.text,
            "confuser_page": openings[other.source],
            "confuser": other.text,
        }
        key = cache.key(prompt.name, prompt.version, model.model_id, *values.values())
        answer = cache.get(key)
        if answer is None:
            raw = model.text(prompt.system, prompt.render(**values), 600)
            answer = cache.put(key, parse_json_answer(raw))
        return answer

    with ThreadPoolExecutor(workers) as executor:
        answers = list(executor.map(one, pairs))
    keeper = Keeper(validation)
    declined = 0
    for (chunk, other, _), entry in zip(pairs, answers, strict=True):
        question = str(entry.get("question") or "").strip()
        if not question or question.lower() == "null":
            declined += 1
            continue
        if not keeper.accept(question, chunk.text):
            continue
        validation.items.append(
            Item(
                id=item_id("contextual", question),
                text=question,
                stratum="contextual",
                answer=str(entry.get("answer") or ""),
                source=chunk.source,
                origin=[chunk.chunk_id],
                confuser=other.chunk_id,
            )
        )
    similarities = sorted(pair[2] for pair in pairs)
    report = {
        "pairs": len(pairs),
        "median_similarity": round(similarities[len(similarities) // 2], 3)
        if similarities
        else 0.0,
        "declined": declined,
        **keeper.report,
    }
    validation.log(
        "contextual",
        prompt=f"{prompt.name}.v{prompt.version}",
        backend=backend,
        **report,
    )
    return report


def import_queries(validation: ValidationSet, lines: list[str]) -> dict:
    """Add real queries, one per non-empty line; duplicates are dropped."""
    keeper = Keeper(validation)
    for line in lines:
        question = line.strip()
        if not question or not keeper.accept(question, ""):
            continue
        validation.items.append(
            Item(id=item_id("real", question), text=question, stratum="real")
        )
    report = dict(keeper.report)
    validation.log("import", **report)
    return report


def assign_split(key: str, dev_share: float, seed: int) -> str:
    digest = hashlib.sha256(f"{seed}|{key}".encode()).hexdigest()
    return "dev" if int(digest[:8], 16) / 0xFFFFFFFF < dev_share else "test"


def split_digest(validation: ValidationSet) -> str:
    rows = sorted(f"{item.id}={item.split}" for item in validation.items)
    return hashlib.sha256("\n".join(rows).encode()).hexdigest()[:16]


def freeze_split(
    validation: ValidationSet,
    dev_share: float = DEV_SHARE,
    seed: int = 0,
    force: bool = False,
) -> dict:
    """Assign every item to dev or test by its page, once.

    Items of one page share a split, so no page is in both. An item without a
    page (a real query) is assigned by its own text. Refuses to change a frozen
    split unless ``force``; new items join by the same rule.
    """
    if validation.split and not force:
        frozen = validation.split
        if frozen["dev_share"] != dev_share or frozen["seed"] != seed:
            raise ValueError("the split is frozen; pass --force to redo it")
    for item in validation.items:
        if not item.split or force:
            item.split = assign_split(item.source or item.text, dev_share, seed)
    counts = Counter(item.split for item in validation.items)
    validation.split = {
        "dev_share": dev_share,
        "seed": seed,
        "digest": split_digest(validation),
        "dev": counts["dev"],
        "test": counts["test"],
    }
    validation.log("split", **validation.split)
    return validation.split


def pool(
    validation: ValidationSet,
    chunks: list[Chunk],
    backends: list[str],
    tag_fields: list[str] | None = None,
    depth: int = POOL_DEPTH,
) -> dict:
    """Pool the top ``depth`` of every system for every item.

    Systems: each backend, vector-only and hybrid, without tags and, when
    ``tag_fields`` are given, with tags in the embedding and the keyword index.
    The item's origin chunks are always in the pool.
    """
    from duckdb_vectors.embedders.registry import resolve_embedder

    systems = 0
    pooled: dict[str, dict[str, None]] = {
        item.id: dict.fromkeys(item.origin) for item in validation.items
    }
    variants: list[tuple[list[str], bool]] = [([], False)]
    if tag_fields:
        variants.append((tag_fields, True))
    with tempfile.TemporaryDirectory() as directory:
        for backend in backends:
            embedder = CachedEmbedder(resolve_embedder(backend))
            for index, (fields, bm25) in enumerate(variants):
                db = Path(directory) / f"{abs(hash(backend))}-{index}.duckdb"
                store = build_index(chunks, db, backend, fields, bm25, embedder)
                for hybrid in (False, True):
                    systems += 1
                    for item in validation.items:
                        hits = store.query(item.text, top_k=depth, hybrid=hybrid)
                        for hit in hits:
                            pooled[item.id][hit.chunk.chunk_id] = None
                store.close()
    sizes = []
    for item in validation.items:
        item.pooled = list(pooled[item.id])
        sizes.append(len(item.pooled))
    report = {
        "systems": systems,
        "depth": depth,
        "backends": backends,
        "tag_fields": tag_fields or [],
        "median_pool": sorted(sizes)[len(sizes) // 2] if sizes else 0,
    }
    validation.log("pool", **report)
    return report


def judge(
    validation: ValidationSet,
    chunks: list[Chunk],
    model: Model,
    cache: AnswerCache,
    workers: int = 8,
    keep_native: bool = True,
) -> dict:
    """Grade every pooled passage and derive golden chunks.

    Golden chunks are those graded 1 or 2. A generated item whose origin passage
    is graded 0 is marked invalid, since its own passage does not answer it. An
    item with native golden chunks (from a public dataset) keeps them when
    ``keep_native``; the judged ones are recorded in ``grades`` for a
    sensitivity analysis.
    """
    prompt = load_prompt("relevance-judge")
    by_id = {chunk.chunk_id: chunk for chunk in chunks}
    tasks = [
        (item, chunk_id)
        for item in validation.items
        for chunk_id in item.pooled
        if chunk_id not in item.grades and chunk_id in by_id
    ]

    def one(task: tuple[Item, str]) -> int | None:
        item, chunk_id = task
        chunk = by_id[chunk_id]
        values = {
            "question": item.text,
            "answer": item.answer,
            "page": chunk.source,
            "passage": chunk.text,
        }
        key = cache.key(prompt.name, prompt.version, model.model_id, *values.values())
        answer = cache.get(key)
        if answer is None:
            try:
                raw = model.text(prompt.system, prompt.render(**values), 200)
            except Deferred:
                return None  # queued for a batch job
            answer = cache.put(key, parse_json_answer(raw))
        grade = int(answer.get("grade", 0))
        return grade if grade in (0, 1, 2) else 0

    with ThreadPoolExecutor(workers) as executor:
        grades = list(executor.map(one, tasks))
    deferred = sum(grade is None for grade in grades)
    if deferred:
        return {"judged": len(tasks) - deferred, "deferred": deferred}
    for (item, chunk_id), grade in zip(tasks, grades, strict=True):
        assert grade is not None  # deferred grades returned above
        item.grades[chunk_id] = grade
    invalid = 0
    for item in validation.items:
        judged = [cid for cid in item.pooled if item.grades.get(cid, 0) >= 1]
        if item.stratum == "native" and keep_native:
            continue
        if item.origin and not any(item.grades.get(c, 0) >= 1 for c in item.origin):
            item.valid = False
            invalid += 1
        item.golden = judged
        item.valid = item.valid and bool(judged)
    golden_sizes = sorted(len(item.golden) for item in validation.items if item.valid)
    report = {
        "judged": len(tasks),
        "graded": dict(Counter(grades)),
        "invalid_items": invalid,
        "items_without_golden": sum(1 for i in validation.items if not i.golden),
        "median_golden": golden_sizes[len(golden_sizes) // 2] if golden_sizes else 0,
        "usage": model.usage.report(),
    }
    validation.log("judge", prompt=f"{prompt.name}.v{prompt.version}", **report)
    return report


AUDIT_COLUMNS = [
    "item",
    "stratum",
    "question",
    "reference_answer",
    "chunk",
    "page",
    "passage",
    "judge_grade",
    "human_grade",
    "question_valid",
]


def audit_rows(
    validation: ValidationSet,
    chunks: list[Chunk],
    size: int = 60,
    per_item: int = 3,
    seed: int = 0,
) -> list[dict]:
    """A balanced sample for a person to grade, without the judge's grades.

    ``size`` items, spread evenly over the strata; per item, its origin chunk,
    a pooled chunk the judge kept and one it rejected. The judge's grade goes in
    a column the person should hide while grading.
    """
    rng = random.Random(seed)
    by_id = {chunk.chunk_id: chunk for chunk in chunks}
    strata: dict[str, list[Item]] = {}
    for item in validation.items:
        if item.grades:
            strata.setdefault(item.stratum, []).append(item)
    share = max(1, size // max(1, len(strata)))
    picked: list[Item] = []
    for items in strata.values():
        picked += rng.sample(items, min(share, len(items)))
    rows = []
    for item in picked:
        kept = [c for c in item.pooled if item.grades.get(c, 0) >= 1]
        rejected = [c for c in item.pooled if item.grades.get(c, -1) == 0]
        chosen = list(dict.fromkeys(item.origin[:1]))
        for group in (kept, rejected):
            rest = [c for c in group if c not in chosen]
            if rest:
                chosen.append(rng.choice(rest))
        for chunk_id in chosen[:per_item]:
            chunk = by_id.get(chunk_id)
            if chunk is None:
                continue
            rows.append(
                {
                    "item": item.id,
                    "stratum": item.stratum,
                    "question": item.text,
                    "reference_answer": item.answer,
                    "chunk": chunk_id,
                    "page": chunk.source,
                    "passage": chunk.text,
                    "judge_grade": item.grades.get(chunk_id, ""),
                    "human_grade": "",
                    "question_valid": "",
                }
            )
    return rows


AUDIT_HEADER = """\
Audit of the relevance judge. Grade each passage blind, then run the kappa step.

human_grade: 2 if the passage answers the question, 1 if it answers part of
it or gives evidence needed for the answer, 0 if it does not help.
question_valid: yes if a real user of this knowledge base would ask the
question, no otherwise. The reference answer is what the question was written
from; a passage need not repeat it word for word.
"""
AUDIT_HINTS = {"question_valid": "yes or no", "human_grade": "0, 1 or 2"}


def audit_sheet(rows: list[dict]) -> list[dict]:
    """The blind sheet: one record per question, with its passages to grade."""
    records: dict[str, dict] = {}
    for row in rows:
        record = records.setdefault(
            row["item"],
            {
                "item": row["item"],
                "stratum": row["stratum"],
                "question": row["question"],
                "reference_answer": row["reference_answer"],
                "question_valid": None,
                "passages": [],
            },
        )
        record["passages"].append(
            {
                "human_grade": None,
                "chunk": row["chunk"],
                "page": row["page"],
                "passage": row["passage"],
            }
        )
    return list(records.values())


def sheet_rows(records: list[dict]) -> list[dict]:
    """The graded sheet back as rows of ``item``, ``chunk`` and the grades."""
    return [
        {
            "item": record["item"],
            "chunk": passage["chunk"],
            "human_grade": passage.get("human_grade"),
            "question_valid": record.get("question_valid"),
        }
        for record in records
        for passage in record.get("passages") or []
    ]


def _filled(value: object) -> bool:
    return value is not None and str(value).strip() != ""


def cohen_kappa(a: list[int], b: list[int]) -> float:
    """Cohen's kappa of two raters over the same items."""
    if not a or len(a) != len(b):
        raise ValueError("kappa needs two rating lists of the same, non-zero length")
    labels = sorted(set(a) | set(b))
    total = len(a)
    observed = sum(1 for x, y in zip(a, b, strict=True) if x == y) / total
    expected = sum((a.count(lab) / total) * (b.count(lab) / total) for lab in labels)
    return 1.0 if expected == 1 else (observed - expected) / (1 - expected)


def agreement(rows: list[dict]) -> dict:
    """Judge-versus-person agreement from a graded audit sheet."""
    graded = [r for r in rows if _filled(r.get("human_grade"))]
    judge_grades = [int(r["judge_grade"]) for r in graded]
    human = [int(r["human_grade"]) for r in graded]
    validity = [
        str(r["question_valid"]).strip().lower() in ("1", "yes", "y", "true")
        for r in {
            r["item"]: r for r in graded if _filled(r.get("question_valid"))
        }.values()
    ]
    return {
        "rows": len(graded),
        "kappa_graded": round(cohen_kappa(judge_grades, human), 3),
        "kappa_binary": round(
            cohen_kappa(
                [int(g >= 1) for g in judge_grades], [int(g >= 1) for g in human]
            ),
            3,
        ),
        "questions_rated": len(validity),
        "questions_valid": round(sum(validity) / len(validity), 3)
        if validity
        else None,
    }


def main(argv: list[str] | None = None) -> int:
    global MIN_WORDS
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument(
        "step",
        choices=[
            "local",
            "contextual",
            "import",
            "split",
            "pool",
            "judge",
            "audit",
            "kappa",
        ],
    )
    parser.add_argument("folder", type=Path)
    parser.add_argument("--work", type=Path, default=Path("tmp/kb"))
    parser.add_argument("--size", type=int, default=200)
    parser.add_argument("--per-chunk", type=int, default=2)
    parser.add_argument(
        "--min-words",
        type=int,
        default=MIN_WORDS,
        help="shortest passage to write a question from (local, contextual)",
    )
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--queries", type=Path, help="text file for import")
    parser.add_argument("--dev-share", type=float, default=DEV_SHARE)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--backends", nargs="+", default=list(DEFAULT_BACKENDS))
    parser.add_argument("--tag-field", dest="tag_fields", action="append", default=[])
    parser.add_argument("--depth", type=int, default=POOL_DEPTH)
    parser.add_argument("--sheet", type=Path, help="audit sheet (audit, kappa)")
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument(
        "--batch",
        action="store_true",
        help="queue model requests for a Bedrock batch job (scripts/kb_batch.py)",
    )
    args = parser.parse_args(argv)
    MIN_WORDS = args.min_words
    if args.batch and args.step != "judge":
        parser.error("--batch applies to the judge step only")
    work = args.work
    validation = load_set(work, corpus=args.folder.name)
    sheet = args.sheet or work / "audit.yaml"
    key_file = sheet.with_name(sheet.stem + "-judge.yaml")
    if args.step == "kappa":
        graded = sheet_rows(read_sheet(sheet))
        judged = {
            (r["item"], r["chunk"]): r["judge_grade"] for r in read_sheet(key_file)
        }
        for row in graded:
            row["judge_grade"] = judged[(row["item"], row["chunk"])]
        report = agreement(graded)
        validation.log("kappa", **report)
        save_set(work, validation)
        print(report)
        return 0
    if args.step == "split":
        print(freeze_split(validation, args.dev_share, args.seed, args.force))
        save_set(work, validation)
        return 0
    if args.step == "import":
        if not args.queries:
            parser.error("import needs --queries")
        lines = args.queries.read_text(encoding="utf-8").splitlines()
        print(import_queries(validation, lines))
        save_set(work, validation)
        return 0
    chunks = chunk_folder(args.folder)
    if args.step == "audit":
        rows = audit_rows(validation, chunks, args.size, seed=args.seed)
        # The person grades a blind sheet; the judge's grades sit in a key file.
        write_sheet(sheet, audit_sheet(rows), AUDIT_HEADER, AUDIT_HINTS)
        write_sheet(
            key_file,
            [{k: row[k] for k in ("item", "chunk", "judge_grade")} for row in rows],
            "The judge's grades of audit.yaml: do not open before grading.",
        )
        print(f"{len(rows)} rows written to {sheet} (key: {key_file})", file=sys.stderr)
        return 0
    if args.step == "pool":
        print(pool(validation, chunks, args.backends, args.tag_fields, args.depth))
        save_set(work, validation)
        return 0
    queue = BatchQueue(work / "batch") if args.batch else None
    model = Model(args.model, args.region, batch=queue)
    cache = AnswerCache(work / "cache" / "validation", model.usage)
    if args.step == "local":
        report = generate_local(
            validation, chunks, model, cache, args.size, args.per_chunk, args.seed
        )
    elif args.step == "contextual":
        report = generate_contextual(
            validation,
            args.folder,
            chunks,
            model,
            cache,
            args.size,
            args.backends[0],
            args.seed,
        )
    else:
        report = judge(validation, chunks, model, cache, args.workers)
    report["usage"] = model.usage.report()
    print(report)
    if queue is not None and queue.deferred:
        print(
            f"{queue.save()} requests wait in {queue.directory}; run "
            "scripts/kb_batch.py submit, then fetch, then this step again.",
            file=sys.stderr,
        )
        return 3
    save_set(work, validation)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

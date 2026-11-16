"""Generate evaluation questions for a corpus that has none.

A language model writes questions from one passage at a time, with nothing but
the passage (no tags, no page name), and the passage's chunk is the golden
chunk. The questions are never shown to the tagging step, and the tagging step
never sees them. Answers are cached by content, so a re-run costs nothing.

    $ .venv/bin/python -m benchmarks.tagged_retrieval.queries FOLDER
"""

import argparse
import hashlib
import json
import random
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel

from benchmarks.tagged_retrieval.enrich import (
    DEFAULT_MODEL,
    DEFAULT_REGION,
    Invoke,
    make_bedrock_invoke,
    parse_json_answer,
)
from benchmarks.tagged_retrieval.prompts import load_prompt
from duckdb_vectors.chunkers.registry import resolve_chunker
from duckdb_vectors.models import Chunk

MIN_WORDS = 60
QUESTIONS_PER_CHUNK = 2
MAX_COPIED_RUN = 4
_WORD = re.compile(r"\w+", re.UNICODE)


class Generated(BaseModel):
    """A generated question and the chunk it was written from."""

    question: str
    chunk_id: str


def words(text: str) -> list[str]:
    return [word.lower() for word in _WORD.findall(text)]


def longest_copied_run(question: str, passage: str) -> int:
    """Longest run of consecutive words the question shares with the passage."""
    q, p = words(question), words(passage)
    best = 0
    positions: dict[str, list[int]] = {}
    for index, word in enumerate(p):
        positions.setdefault(word, []).append(index)
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


def sample_chunks(chunks: list[Chunk], size: int, seed: int = 0) -> list[Chunk]:
    """A seeded sample of the chunks long enough to carry a question."""
    eligible = [c for c in chunks if len(c.text.split()) >= MIN_WORDS]
    return random.Random(seed).sample(eligible, min(size, len(eligible)))


def generate_for_chunk(chunk: Chunk, invoke: Invoke, cache: Path) -> list[str]:
    """The questions for one chunk, cached by prompt version and passage."""
    prompt = load_prompt("query-generation")
    key = hashlib.sha256(
        f"{prompt.version}|{QUESTIONS_PER_CHUNK}|{chunk.text}".encode()
    ).hexdigest()
    entry = cache / f"{key}.json"
    if entry.exists():
        return json.loads(entry.read_text(encoding="utf-8"))
    answer = parse_json_answer(
        invoke(
            prompt.system,
            prompt.render(passage=chunk.text, count=str(QUESTIONS_PER_CHUNK)),
        )
    )
    questions = [q.strip() for q in answer.get("questions", []) if str(q).strip()]
    entry.write_text(json.dumps(questions), encoding="utf-8")
    return questions


def generate(
    chunks: list[Chunk], invoke: Invoke, cache: Path, workers: int = 8
) -> tuple[list[Generated], dict[str, int]]:
    """Generate questions for every chunk and report their quality.

    The report counts questions, exact duplicates (dropped), questions that
    copy more than four consecutive words from their passage (dropped, the
    prompt forbids it), and questions that name the page's ``topic`` (kept, but
    counted, because they could favour a tag that repeats it).
    """
    cache.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(workers) as pool:
        answers = list(pool.map(lambda c: generate_for_chunk(c, invoke, cache), chunks))
    report = {
        "chunks": len(chunks),
        "questions": 0,
        "duplicates": 0,
        "copied_too_much": 0,
        "mention_topic": 0,
        "kept": 0,
    }
    seen: set[str] = set()
    kept: list[Generated] = []
    for chunk, questions in zip(chunks, answers, strict=True):
        topic = str(chunk.metadata.get("topic", "")).lower()
        for question in questions:
            report["questions"] += 1
            normalized = " ".join(words(question))
            if normalized in seen:
                report["duplicates"] += 1
                continue
            if longest_copied_run(question, chunk.text) > MAX_COPIED_RUN:
                report["copied_too_much"] += 1
                continue
            seen.add(normalized)
            report["mention_topic"] += bool(topic) and topic in question.lower()
            report["kept"] += 1
            kept.append(Generated(question=question, chunk_id=chunk.chunk_id))
    return kept, report


def chunk_folder(folder: Path) -> list[Chunk]:
    """The folder's chunks, as the package indexes them."""
    return list(resolve_chunker("composite").chunk_folder(folder, recursive=True))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--work", type=Path, default=Path("tmp/enrichment"))
    parser.add_argument("--chunks", type=int, default=400)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--region", default=DEFAULT_REGION)
    args = parser.parse_args(argv)
    chunks = sample_chunks(chunk_folder(args.folder), args.chunks)
    invoke = make_bedrock_invoke(args.model, args.region)
    kept, report = generate(chunks, invoke, args.work / "query-cache")
    output = {
        "prompt_version": load_prompt("query-generation").version,
        "model": args.model,
        "quality": report,
        "queries": [item.model_dump() for item in kept],
    }
    (args.work / "queries.json").write_text(json.dumps(output, indent=1), "utf-8")
    print(json.dumps(report, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

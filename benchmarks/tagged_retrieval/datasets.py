"""Dataset loaders: a corpus of chunks with native tags, and queries with
golden chunks.

The Anthropic cookbook set is used chunk for chunk, as published, so a baseline
here is comparable with the cookbook's own Pass@k. Its corpus file carries no
metadata; the repository and path of each file exist only in the evaluation
file, in ``golden_documents[].meta``, and are joined back on the document uuid.
"""

import hashlib
import json
import urllib.request
from pathlib import Path, PurePosixPath

from pydantic import BaseModel

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


class Dataset(BaseModel):
    """A corpus, its queries and where it came from."""

    name: str
    chunks: list[Chunk]
    queries: list[Query]
    sources: dict[str, str]


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

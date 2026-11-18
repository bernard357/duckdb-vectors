"""Shared helpers of the knowledge-base curation scripts (``scripts/kb_*.py``).

The curation scripts call a language model once, upstream of the package, on a
folder of Markdown pages: never at chunking or indexing time. This module holds
what they share: the versioned prompts in ``scripts/prompts/``, the Bedrock
client with usage and cost accounting, a content-addressed answer cache, the
front matter editor, and the chunking of a folder exactly as the package does.
"""

import hashlib
import json
import re
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel

from duckdb_vectors.chunkers.registry import resolve_chunker
from duckdb_vectors.models import Chunk

REPO_ROOT = Path(__file__).resolve().parent.parent
PROMPTS = Path(__file__).resolve().parent / "prompts"
DEFAULT_MODEL = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"
DEFAULT_REGION = "eu-central-1"
# USD per million tokens for Claude Haiku 4.5 on Bedrock: input, output, cache
# write, cache read. Used for the cost estimate a run reports; check the price
# list before relying on it.
PRICES = {"input": 1.0, "output": 5.0, "cache_write": 1.25, "cache_read": 0.10}
_PLACEHOLDER = re.compile(r"(?<!\{)\{([a-z_]+)\}(?!\})")
_FRONT_MATTER = re.compile(r"\A---\n(.*?\n)?---\n", re.S)


class Prompt(BaseModel):
    """One versioned prompt: front matter, a system part and a user part."""

    name: str
    version: int
    inputs: list[str]
    sees_queries: bool
    sees_tags: bool = True
    system: str
    user: str

    def placeholders(self) -> set[str]:
        """Names of the ``{placeholders}`` in the user section."""
        return set(_PLACEHOLDER.findall(self.user))

    def render(self, **values: str) -> str:
        """Fill the user section; every declared input must be given."""
        missing = set(self.inputs) - set(values)
        if missing:
            raise ValueError(f"missing inputs for {self.name}: {sorted(missing)}")
        return _PLACEHOLDER.sub(lambda m: str(values[m.group(1)]), self.user)


def load_prompt(name: str, version: int = 1, directory: Path = PROMPTS) -> Prompt:
    """Read ``{name}.v{version}.md`` from ``scripts/prompts/``."""
    text = (directory / f"{name}.v{version}.md").read_text(encoding="utf-8")
    _, front, body = text.split("---\n", 2)
    meta = yaml.safe_load(front)
    system, _, user = body.split("## System", 1)[1].partition("## User")
    return Prompt(
        name=meta["prompt"],
        version=meta["version"],
        inputs=meta["inputs"],
        sees_queries=meta["sees_queries"],
        sees_tags=meta.get("sees_tags", True),
        system=system.strip(),
        user=user.strip(),
    )


class Usage(BaseModel):
    """Model calls and tokens of a run, and answers served from the cache."""

    calls: int = 0
    cached_answers: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    cache_write_tokens: int = 0
    cache_read_tokens: int = 0

    def usd(self) -> float:
        """Estimated cost in USD, from :data:`PRICES`."""
        return (
            self.input_tokens * PRICES["input"]
            + self.output_tokens * PRICES["output"]
            + self.cache_write_tokens * PRICES["cache_write"]
            + self.cache_read_tokens * PRICES["cache_read"]
        ) / 1_000_000

    def report(self) -> dict:
        return {**self.model_dump(), "usd_estimate": round(self.usd(), 4)}


# ``invoke(system, blocks, max_tokens) -> text``; ``blocks`` are Converse
# content blocks, so a caller can put a cache point after a long prefix.
Invoke = Callable[[str, list[dict], int], str]


class Model:
    """A language model behind :data:`Invoke`, with usage accounting.

    ``invoke`` defaults to Bedrock; tests pass a fake. Thread safe.
    """

    def __init__(
        self,
        model_id: str = DEFAULT_MODEL,
        region: str = DEFAULT_REGION,
        invoke: Invoke | None = None,
        session: Any = None,
    ) -> None:
        self.model_id = model_id
        self.usage = Usage()
        self._lock = threading.Lock()
        self._invoke = invoke or self._bedrock(model_id, region, session)

    def _bedrock(self, model_id: str, region: str, session: Any) -> Invoke:
        import boto3
        from botocore.config import Config

        session = session or boto3.Session()
        client = session.client(
            "bedrock-runtime",
            region_name=region,
            config=Config(retries={"mode": "adaptive", "max_attempts": 10}),
        )

        def invoke(system: str, blocks: list[dict], max_tokens: int) -> str:
            response = client.converse(
                modelId=model_id,
                system=[{"text": system}],
                messages=[{"role": "user", "content": blocks}],
                inferenceConfig={"maxTokens": max_tokens, "temperature": 0},
            )
            usage = response.get("usage", {})
            with self._lock:
                self.usage.input_tokens += usage.get("inputTokens", 0)
                self.usage.output_tokens += usage.get("outputTokens", 0)
                self.usage.cache_read_tokens += usage.get("cacheReadInputTokens", 0)
                self.usage.cache_write_tokens += usage.get("cacheWriteInputTokens", 0)
            return response["output"]["message"]["content"][0]["text"].strip()

        return invoke

    def __call__(self, system: str, blocks: list[dict], max_tokens: int = 600) -> str:
        with self._lock:
            self.usage.calls += 1
        return self._invoke(system, blocks, max_tokens)

    def text(self, system: str, user: str, max_tokens: int = 600) -> str:
        """Call with a single text block."""
        return self(system, [{"text": user}], max_tokens)


class AnswerCache:
    """Answers stored on disk under the hash of everything that produced them."""

    def __init__(self, directory: Path, usage: Usage | None = None) -> None:
        self.directory = directory
        self.usage = usage
        directory.mkdir(parents=True, exist_ok=True)

    @staticmethod
    def key(*parts: object) -> str:
        return hashlib.sha256("\x1f".join(map(str, parts)).encode()).hexdigest()

    def get(self, key: str) -> Any:
        entry = self.directory / f"{key}.json"
        if not entry.exists():
            return None
        if self.usage is not None:
            self.usage.cached_answers += 1
        return json.loads(entry.read_text(encoding="utf-8"))

    def put(self, key: str, value: Any) -> Any:
        (self.directory / f"{key}.json").write_text(
            json.dumps(value, ensure_ascii=False), encoding="utf-8"
        )
        return value


def parse_json_answer(text: str) -> Any:
    """Parse the JSON object a model answered with, tolerating code fences."""
    cleaned = text.strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError(f"no JSON object in the answer: {text[:120]!r}")
    return json.loads(cleaned[start : end + 1])


def pages(folder: Path) -> list[Path]:
    """The Markdown pages of a folder, recursively, in a stable order."""
    return sorted(folder.rglob("*.md"))


def split_front_matter(text: str) -> tuple[str, str]:
    """Return ``(front_matter_text, body)``; the front matter is empty if none."""
    match = _FRONT_MATTER.match(text)
    if not match:
        return "", text
    return match.group(1) or "", text[match.end() :]


def read_front_matter(text: str) -> dict:
    """The parsed front matter of a page, or an empty mapping."""
    front, _ = split_front_matter(text)
    try:
        parsed = yaml.safe_load(front) if front else {}
    except yaml.YAMLError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def set_front_matter_key(text: str, key: str, value: Any) -> str:
    """Return the page with the top-level front matter ``key`` set to ``value``.

    Only that key's block is rewritten, at the end of the front matter; every
    other line is kept as written. ``None`` removes the key. A page without
    front matter gains one.
    """
    front, body = split_front_matter(text)
    kept: list[str] = []
    skipping = False
    for line in front.splitlines():
        if line.startswith(f"{key}:"):
            skipping = True
            continue
        if skipping and (line.startswith((" ", "\t", "-")) or not line.strip()):
            continue
        skipping = False
        kept.append(line)
    if value is not None:
        dumped = yaml.safe_dump(
            {key: value}, sort_keys=False, allow_unicode=True, width=88
        )
        kept.extend(dumped.rstrip("\n").splitlines())
    if not kept:
        return body
    return "---\n" + "\n".join(kept) + "\n---\n" + body


def chunk_folder(folder: Path) -> list[Chunk]:
    """The folder's chunks, exactly as ``duckdb-vectors index`` cuts them."""
    return list(resolve_chunker("composite").chunk_folder(folder, recursive=True))


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=1, ensure_ascii=False), encoding="utf-8")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def build_index(
    chunks: list[Chunk],
    db_path: Path,
    backend: str,
    tag_fields: list[str] | None = None,
    tag_bm25: bool = False,
    embedder: Any = None,
    embed_tags: bool = True,
) -> Any:
    """Index ``chunks`` into a fresh store at ``db_path`` and reopen it.

    The same write path as ``duckdb-vectors index``: tagged text is embedded and
    fingerprinted, and the keyword index holds tags when ``tag_bm25`` is set.
    ``embed_tags=False`` embeds the raw chunk text even with ``tag_fields``, to
    put tags in the keyword index only. ``embedder`` lets several indexes share
    one loaded (or caching) model.
    """
    import datetime

    from duckdb_vectors.config import Settings
    from duckdb_vectors.etag import compute_etag
    from duckdb_vectors.store import VectorStore

    settings = Settings(
        db_path=str(db_path),
        backend=backend,
        tag_fields=tag_fields or [],
        tag_bm25=tag_bm25,
    )
    store = VectorStore(settings=settings, embedder=embedder)
    texts = store.embedded_texts(chunks) if embed_tags else [c.text for c in chunks]
    vectors = store.embedder.embed(texts)
    rows = [
        (
            chunk,
            compute_etag(text, store.embedder.model_identifier, store.dimensions),
            v,
        )
        for chunk, text, v in zip(chunks, texts, vectors, strict=True)
    ]
    store._write_rows(rows, datetime.datetime.now(datetime.UTC))
    store.close()
    return VectorStore(settings=settings, embedder=store.embedder)


class CachedEmbedder:
    """Embeds each distinct text once and shares the vectors between indexes."""

    def __init__(self, embedder: Any) -> None:
        self._embedder = embedder
        self._cache: dict[str, list[float]] = {}

    @property
    def model_identifier(self) -> str:
        return self._embedder.model_identifier

    @property
    def dimensions(self) -> int:
        return self._embedder.dimensions

    def embed(self, texts: list[str]) -> list[list[float]]:
        missing = list(dict.fromkeys(t for t in texts if t not in self._cache))
        if missing:
            for text, vector in zip(
                missing, self._embedder.embed(missing), strict=True
            ):
                self._cache[text] = vector
        return [self._cache[text] for text in texts]

"""Write the LLM context of Contextual Retrieval for the Anthropic chunks.

This is the reference condition for hypothesis H4: Anthropic's published method,
one model call per chunk with the whole document in view, used here to measure
how much of its gain deterministic tags recover. It is not part of the package.
The document is sent as a cached prompt prefix, so each document is paid for
once and then read from the cache for its other chunks.

    $ .venv/bin/python -m benchmarks.tagged_retrieval.contextualize
"""

import argparse
import hashlib
import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from benchmarks.tagged_retrieval.datasets import Dataset, load_anthropic
from benchmarks.tagged_retrieval.enrich import DEFAULT_MODEL, DEFAULT_REGION
from benchmarks.tagged_retrieval.prompts import load_prompt

BlocksInvoke = Callable[[str, list[dict]], str]
_CACHE = Path(__file__).resolve().parent / ".cache"
_SPLIT = "Here is the chunk we want to situate"


def make_bedrock_blocks_invoke(
    model_id: str = DEFAULT_MODEL, region: str = DEFAULT_REGION, session=None
) -> BlocksInvoke:
    """``invoke(system, content_blocks) -> text`` through the Converse API."""
    import boto3
    from botocore.config import Config

    session = session or boto3.Session()
    client = session.client(
        "bedrock-runtime",
        region_name=region,
        config=Config(retries={"mode": "adaptive", "max_attempts": 8}),
    )

    def invoke(system: str, blocks: list[dict]) -> str:
        response = client.converse(
            modelId=model_id,
            system=[{"text": system}],
            messages=[{"role": "user", "content": blocks}],
            inferenceConfig={"maxTokens": 300, "temperature": 0},
        )
        return response["output"]["message"]["content"][0]["text"].strip()

    return invoke


def blocks_for(document: str, chunk: str, cached: bool = True) -> list[dict]:
    """The user message: the document (cacheable), then the chunk."""
    prompt = load_prompt("contextual-header")
    text = prompt.render(document=document, chunk=chunk)
    head, marker, tail = text.partition(_SPLIT)
    blocks = [{"text": head.rstrip()}]
    if cached:
        blocks.append({"cachePoint": {"type": "default"}})
    blocks.append({"text": marker + tail})
    return blocks


def contextualize(
    dataset: Dataset, invoke: BlocksInvoke, cache: Path, workers: int = 8
) -> dict[str, str]:
    """The context of every chunk, cached by prompt, document and chunk."""
    prompt = load_prompt("contextual-header")
    cache.mkdir(parents=True, exist_ok=True)
    by_source: dict[str, list] = {}
    for chunk in dataset.chunks:
        by_source.setdefault(chunk.source, []).append(chunk)

    def one_document(source: str) -> dict[str, str]:
        document = dataset.documents[source]
        result = {}
        for chunk in by_source[source]:
            key = hashlib.sha256(
                f"{prompt.version}|{document}|{chunk.text}".encode()
            ).hexdigest()
            entry = cache / f"{key}.txt"
            if entry.exists():
                result[chunk.chunk_id] = entry.read_text(encoding="utf-8")
                continue
            context = invoke(prompt.system, blocks_for(document, chunk.text))
            entry.write_text(context, encoding="utf-8")
            result[chunk.chunk_id] = context
        return result

    with ThreadPoolExecutor(workers) as pool:
        parts = list(pool.map(one_document, sorted(by_source)))
    return {chunk_id: text for part in parts for chunk_id, text in part.items()}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--region", default=DEFAULT_REGION)
    args = parser.parse_args(argv)
    dataset = load_anthropic(_CACHE)
    contexts = contextualize(
        dataset,
        make_bedrock_blocks_invoke(args.model, args.region),
        _CACHE / "contexts",
    )
    out = {"model": args.model, "prompt_version": 1, "contexts": contexts}
    (_CACHE / "anthropic-contexts.json").write_text(json.dumps(out), encoding="utf-8")
    sizes = sorted(len(text.split()) for text in contexts.values())
    print(f"{len(contexts)} contexts, median {sizes[len(sizes) // 2]} words")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

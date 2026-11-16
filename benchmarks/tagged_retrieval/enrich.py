"""Enrich a Markdown folder with derived tags, using a language model once.

The model works upstream of the package, never in the indexing path: it proposes
a tag schema from a sample of the corpus (stage 1), fills that schema for every
page (stage 2), and the result is written into each page's front matter under an
``llm`` key, so native tags and derived tags stay separate and can be compared.
Answers are cached on disk by content, so a re-run costs nothing and a changed
page, schema, prompt or model costs one call.

    $ .venv/bin/python -m benchmarks.tagged_retrieval.enrich discover FOLDER
    $ .venv/bin/python -m benchmarks.tagged_retrieval.enrich tag FOLDER
    $ .venv/bin/python -m benchmarks.tagged_retrieval.enrich apply FOLDER

Calls go through Amazon Bedrock with a geographic ``eu.`` inference profile
called from an EU region, so requests stay in the European Union.
"""

import argparse
import hashlib
import json
import random
import re
import sys
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel

from benchmarks.tagged_retrieval.prompts import load_prompt

DEFAULT_MODEL = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"
DEFAULT_REGION = "eu-central-1"
NAMESPACE = "llm"
SAMPLE_PAGES = 40
SAMPLE_CHARS = 1500
PAGE_CHARS = 8000
MAX_FIELDS = 6
MAX_FREE_WORDS = 4
# Answers that mean "no value": a tag that says so tells a search nothing.
SENTINELS = frozenset({"other", "none", "n/a", "unknown", "null", "not applicable"})
_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.S)

Invoke = Callable[[str, str], str]


class Field(BaseModel):
    """One tag of the agreed schema; ``values`` is a closed list or ``"free"``."""

    name: str
    description: str
    values: list[str] | str
    why_it_disambiguates: str = ""


class Schema(BaseModel):
    """The tag schema, with the provenance that makes the run reproducible."""

    prompt_version: int
    model: str
    fields: list[Field]
    rejected: list[dict] = []

    def digest(self) -> str:
        payload = json.dumps([f.model_dump() for f in self.fields], sort_keys=True)
        return hashlib.sha256(payload.encode()).hexdigest()[:16]

    def describe(self) -> str:
        lines = []
        for field in self.fields:
            allowed = (
                field.values if field.values == "free" else ", ".join(field.values)
            )
            lines.append(f"- {field.name}: {field.description} Allowed: {allowed}")
        return "\n".join(lines)


def make_bedrock_invoke(
    model_id: str = DEFAULT_MODEL, region: str = DEFAULT_REGION, session=None
) -> Invoke:
    """Return ``invoke(system, user) -> text`` backed by the Bedrock Converse API.

    Pass a boto3 ``session`` to control credentials; adaptive retries absorb
    throttling.
    """
    import boto3
    from botocore.config import Config

    session = session or boto3.Session()
    client = session.client(
        "bedrock-runtime",
        region_name=region,
        config=Config(retries={"mode": "adaptive", "max_attempts": 8}),
    )

    def invoke(system: str, user: str) -> str:
        response = client.converse(
            modelId=model_id,
            system=[{"text": system}],
            messages=[{"role": "user", "content": [{"text": user}]}],
            inferenceConfig={"maxTokens": 1500, "temperature": 0},
        )
        return response["output"]["message"]["content"][0]["text"]

    return invoke


def parse_json_answer(text: str) -> dict:
    """Parse the JSON object a model answered with, tolerating code fences."""
    cleaned = text.strip()
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start < 0 or end < start:
        raise ValueError(f"no JSON object in the answer: {text[:120]!r}")
    return json.loads(cleaned[start : end + 1])


def split_front_matter(text: str) -> tuple[str, str]:
    """Return ``(front_matter_block, body)``; the block is empty without one."""
    match = _FRONT_MATTER.match(text)
    if not match:
        return "", text
    return match.group(1), text[match.end() :]


def pages(folder: Path) -> list[Path]:
    return sorted(folder.rglob("*.md"))


def discover(folder: Path, invoke: Invoke, model: str, seed: int = 0) -> Schema:
    """Stage 1: propose the tag schema from a sample of the folder."""
    prompt = load_prompt("schema-discovery")
    paths = pages(folder)
    sample = random.Random(seed).sample(paths, min(SAMPLE_PAGES, len(paths)))
    excerpts = []
    for path in sorted(sample):
        _, body = split_front_matter(path.read_text(encoding="utf-8"))
        excerpts.append(f"### {path.name}\n{body.strip()[:SAMPLE_CHARS]}")
    answer = parse_json_answer(
        invoke(
            prompt.system,
            prompt.render(
                sample="\n\n".join(excerpts),
                existing_fields="topic",
                max_fields=str(MAX_FIELDS),
            ),
        )
    )
    return Schema(
        prompt_version=prompt.version,
        model=model,
        fields=[Field(**field) for field in answer["fields"]][:MAX_FIELDS],
        rejected=answer.get("rejected", []),
    )


def clean_value(field: Field, value: object) -> str | None:
    """Keep a value only if it honours the schema; otherwise ``None``.

    Sentinel answers such as ``Other`` or ``None`` count as no value.
    """
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    if text.lower() in SENTINELS:
        return None
    if field.values == "free":
        return text if len(text.split()) <= MAX_FREE_WORDS else None
    return text if text in field.values else None


def tag_page(path: Path, schema: Schema, invoke: Invoke, cache: Path) -> dict:
    """Stage 2: the schema's values for one page, cached by content."""
    prompt = load_prompt("tagging")
    _, body = split_front_matter(path.read_text(encoding="utf-8"))
    document = f"{path.name}\n\n{body.strip()[:PAGE_CHARS]}"
    key = hashlib.sha256(
        f"{prompt.version}|{schema.model}|{schema.digest()}|{document}".encode()
    ).hexdigest()
    entry = cache / f"{key}.json"
    if entry.exists():
        return json.loads(entry.read_text(encoding="utf-8"))
    answer = parse_json_answer(
        invoke(
            prompt.system,
            prompt.render(schema=schema.describe(), document=document),
        )
    )
    result = {"raw": answer}
    entry.write_text(json.dumps(result), encoding="utf-8")
    return result


def tag_folder(
    folder: Path, schema: Schema, invoke: Invoke, cache: Path, workers: int = 8
) -> dict[str, dict[str, str | None]]:
    """Tag every page; returns ``{page name: {field: value or None}}``."""
    cache.mkdir(parents=True, exist_ok=True)
    paths = pages(folder)
    with ThreadPoolExecutor(workers) as pool:
        raws = list(pool.map(lambda p: tag_page(p, schema, invoke, cache), paths))
    tags = {}
    for path, result in zip(paths, raws, strict=True):
        tags[str(path.relative_to(folder))] = {
            field.name: clean_value(field, result["raw"].get(field.name))
            for field in schema.fields
        }
    return tags


def render_block(values: dict[str, str | None]) -> str:
    """The ``llm:`` front matter block; ``None`` values are left out."""
    kept = {name: value for name, value in values.items() if value is not None}
    if not kept:
        return ""
    lines = [f"{NAMESPACE}:"] + [f"  {n}: {json.dumps(v)}" for n, v in kept.items()]
    return "\n".join(lines)


def apply_block(text: str, values: dict[str, str | None]) -> str:
    """Return the page with its ``llm`` block set; nothing else is touched."""
    front, body = split_front_matter(text)
    if not front and not text.startswith("---\n"):
        raise ValueError("the page has no front matter")
    lines = front.split("\n") if front else []
    kept, skipping = [], False
    for line in lines:
        if line.startswith(f"{NAMESPACE}:"):
            skipping = True
            continue
        if skipping and (line.startswith(" ") or not line.strip()):
            continue
        skipping = False
        kept.append(line)
    block = render_block(values)
    if block:
        kept.append(block)
    return "---\n" + "\n".join(kept) + "\n---\n" + body


def apply_tags(folder: Path, tags: dict[str, dict[str, str | None]]) -> int:
    """Write the ``llm`` blocks into the pages; returns how many changed."""
    changed = 0
    for name, values in tags.items():
        path = folder / name
        text = path.read_text(encoding="utf-8")
        updated = apply_block(text, values)
        if updated != text:
            path.write_text(updated, encoding="utf-8")
            changed += 1
    return changed


def coverage_report(schema: Schema, tags: dict[str, dict[str, str | None]]) -> dict:
    """Per field: pages tagged, distinct values, and the value counts."""
    report = {}
    for field in schema.fields:
        values = [page.get(field.name) for page in tags.values()]
        filled = [v for v in values if v is not None]
        counts: dict[str, int] = {}
        for value in filled:
            counts[value] = counts.get(value, 0) + 1
        report[field.name] = {
            "pages": len(values),
            "tagged": len(filled),
            "distinct": len(counts),
            "top": sorted(counts.items(), key=lambda kv: -kv[1])[:8],
        }
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("stage", choices=["discover", "tag", "apply"])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--work", type=Path, default=Path("tmp/enrichment"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument(
        "--keep",
        nargs="+",
        default=None,
        help="apply only these fields (for example the ones the tags report keeps)",
    )
    args = parser.parse_args(argv)
    args.work.mkdir(parents=True, exist_ok=True)
    schema_file, tags_file = args.work / "schema.json", args.work / "tags.json"
    if args.stage == "discover":
        invoke = make_bedrock_invoke(args.model, args.region)
        schema = discover(args.folder, invoke, args.model)
        schema_file.write_text(schema.model_dump_json(indent=1), encoding="utf-8")
        print(schema.describe())
        print(f"\nrejected: {[r.get('name') for r in schema.rejected]}")
        return 0
    schema = Schema.model_validate_json(schema_file.read_text(encoding="utf-8"))
    if args.stage == "tag":
        invoke = make_bedrock_invoke(schema.model, args.region)
        tags = tag_folder(args.folder, schema, invoke, args.work / "cache")
        tags_file.write_text(json.dumps(tags, indent=1), encoding="utf-8")
        print(json.dumps(coverage_report(schema, tags), indent=1))
        return 0
    tags = json.loads(tags_file.read_text(encoding="utf-8"))
    if args.keep is not None:
        unknown = set(args.keep) - {field.name for field in schema.fields}
        if unknown:
            parser.error(f"not in the schema: {sorted(unknown)}")
        tags = {
            page: {name: v for name, v in values.items() if name in args.keep}
            for page, values in tags.items()
        }
    print(f"{apply_tags(args.folder, tags)} pages changed", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

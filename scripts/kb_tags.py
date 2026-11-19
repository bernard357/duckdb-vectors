"""Derive page-level tags for a Markdown knowledge base, with a language model.

Steps, each reading and writing files in ``WORK``:

* ``discover``: a model proposes the fields from a sample of pages stratified by
  folder and length (an opening and a middle passage of each), together with
  the dev split of the validation set when one exists, never the test split. It
  is asked for what a passage loses when cut from its page, in the words users
  type, not for taxonomies of form or audience. Catch-all values ("None",
  "Other", ...) are removed from closed lists.
* ``tag``: every page is tagged twice, the second time with the fields in
  reverse order, and the agreement of the two passes is reported per field. A
  field below ``--min-agreement`` is dropped. A ``review.yaml`` sheet lists 30
  pages per field for a person to check.
* ``apply``: the kept fields are written under the page's ``retrieval`` front
  matter key, so derived tags stay apart from native ones. Only that key is touched;
  the script never commits.

    $ make kb-tags KB=path/to/pages WORK=tmp/kb/work
    $ .venv/bin/python -m scripts.kb_tags discover FOLDER --work DIR
"""

import argparse
import logging
import random
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from pydantic import BaseModel

from development.curation import (
    DEFAULT_MODEL,
    DEFAULT_REGION,
    LEGACY_NAMESPACE,
    NAMESPACE,
    AnswerCache,
    Model,
    load_prompt,
    pages,
    parse_json_answer,
    read_front_matter,
    read_json,
    set_front_matter_key,
    split_front_matter,
    write_json,
    write_sheet,
)

SAMPLE_PAGES = 40
EXCERPT_CHARS = 900
PAGE_CHARS = 12000
MAX_FIELDS = 6
MAX_FREE_WORDS = 4
MAX_QUERIES = 80
MIN_AGREEMENT = 0.8
REVIEW_PAGES = 30
SENTINELS = frozenset(
    {"other", "none", "n/a", "na", "unknown", "null", "general", "not applicable"}
)
RESERVED = frozenset({NAMESPACE, LEGACY_NAMESPACE, "sections"})
logger = logging.getLogger(__name__)


class Field(BaseModel):
    """One tag of the schema; ``values`` is a closed list or ``"free"``."""

    name: str
    description: str
    values: list[str] | str
    example_queries: list[str] = []
    lost_context: str = ""


class Schema(BaseModel):
    """The tag schema and the provenance that makes it reproducible."""

    prompt_version: int
    model: str
    fields: list[Field]
    rejected: list[dict] = []
    removed_values: dict[str, list[str]] = {}

    def describe(self, reverse: bool = False) -> str:
        fields = list(reversed(self.fields)) if reverse else self.fields
        lines = []
        for field in fields:
            allowed = (
                field.values if field.values == "free" else ", ".join(field.values)
            )
            lines.append(f"- {field.name}: {field.description} Allowed: {allowed}")
        return "\n".join(lines)


def existing_fields(files: list[Path]) -> list[str]:
    """Top-level front matter keys found in the pages, reserved keys excepted.

    A legacy ``llm`` key, where these scripts wrote before #66, is not a native
    field: it is reported, and ``apply`` moves the fields it holds.
    """
    found: Counter = Counter()
    for path in files:
        found.update(read_front_matter(path.read_text(encoding="utf-8")).keys())
    if found[LEGACY_NAMESPACE]:
        logger.warning(
            "%d pages keep derived tags under the legacy '%s' key; "
            "'apply' moves them to '%s'",
            found[LEGACY_NAMESPACE],
            LEGACY_NAMESPACE,
            NAMESPACE,
        )
    return sorted(key for key in found if key not in RESERVED)


def stratified_sample(files: list[Path], folder: Path, size: int, seed: int) -> list:
    """Up to ``size`` pages spread over folders and over short, mid and long pages."""
    rng = random.Random(seed)
    strata: dict[tuple, list[Path]] = {}
    lengths = {path: path.stat().st_size for path in files}
    ordered = sorted(lengths.values())
    cuts = (
        (ordered[len(ordered) // 3], ordered[2 * len(ordered) // 3])
        if ordered
        else (0, 0)
    )
    for path in files:
        parts = path.relative_to(folder).parts
        top = parts[0] if len(parts) > 1 else ""
        band = sum(lengths[path] > cut for cut in cuts)
        strata.setdefault((top, band), []).append(path)
    for group in strata.values():
        rng.shuffle(group)
    picked: list[Path] = []
    while len(picked) < min(size, len(files)):
        for key in sorted(strata):
            if strata[key] and len(picked) < size:
                picked.append(strata[key].pop())
    return sorted(picked)


def excerpt(path: Path, folder: Path) -> str:
    """The opening and a middle passage of a page."""
    _, body = split_front_matter(path.read_text(encoding="utf-8"))
    body = body.strip()
    head = body[:EXCERPT_CHARS]
    middle = ""
    if len(body) > 3 * EXCERPT_CHARS:
        start = len(body) // 2
        middle = "\n[...]\n" + body[start : start + EXCERPT_CHARS]
    return f"### {path.relative_to(folder)}\n{head}{middle}"


def clean_schema(answer: dict, model: str, version: int) -> Schema:
    """The schema with catch-all values removed and at most ``MAX_FIELDS``."""
    fields, removed = [], {}
    for raw in answer.get("fields", [])[:MAX_FIELDS]:
        field = Field(**{k: v for k, v in raw.items() if k in Field.model_fields})
        if isinstance(field.values, list):
            kept = [v for v in field.values if str(v).strip().lower() not in SENTINELS]
            dropped = [v for v in field.values if v not in kept]
            if dropped:
                removed[field.name] = dropped
            field.values = kept
            if not kept:
                continue
        fields.append(field)
    return Schema(
        prompt_version=version,
        model=model,
        fields=fields,
        rejected=answer.get("rejected", []),
        removed_values=removed,
    )


def discover(
    folder: Path,
    model: Model,
    queries: list[str],
    seed: int = 0,
    size: int = SAMPLE_PAGES,
) -> Schema:
    """Stage 1: propose the schema from a stratified sample and dev queries."""
    prompt = load_prompt("schema-discovery", version=2)
    files = pages(folder)
    sample = stratified_sample(files, folder, size, seed)
    rng = random.Random(seed)
    shown = rng.sample(queries, min(MAX_QUERIES, len(queries))) if queries else []
    answer = parse_json_answer(
        model.text(
            prompt.system,
            prompt.render(
                sample="\n\n".join(excerpt(path, folder) for path in sample),
                queries="\n".join(f"- {q}" for q in shown) or "(none available)",
                existing_fields=", ".join(existing_fields(files)) or "(none)",
                max_fields=str(MAX_FIELDS),
            ),
            3000,
        )
    )
    return clean_schema(answer, model.model_id, prompt.version)


def clean_value(field: Field, value: object) -> str | None:
    """Keep a value only if it honours the schema; otherwise ``None``."""
    if value is None or not str(value).strip():
        return None
    text = str(value).strip()
    if text.lower() in SENTINELS:
        return None
    if field.values == "free":
        return text if len(text.split()) <= MAX_FREE_WORDS else None
    return text if text in field.values else None


def tag_page(
    path: Path, schema: Schema, model: Model, cache: AnswerCache, reverse: bool
) -> dict:
    """The raw answer for one page in one pass, cached by content."""
    prompt = load_prompt("tagging", version=2)
    _, body = split_front_matter(path.read_text(encoding="utf-8"))
    document = f"{path.name}\n\n{body.strip()[:PAGE_CHARS]}"
    described = schema.describe(reverse)
    key = cache.key(prompt.name, prompt.version, model.model_id, described, document)
    answer = cache.get(key)
    if answer is None:
        raw = model.text(
            prompt.system, prompt.render(schema=described, document=document), 800
        )
        answer = cache.put(key, parse_json_answer(raw))
    return answer


def tag_folder(
    folder: Path, schema: Schema, model: Model, cache: AnswerCache, workers: int = 8
) -> tuple[dict, dict]:
    """Tag every page twice; returns ``(tags, agreement)``.

    ``tags`` maps each page to the first pass's clean values, with ``None``
    where the two passes disagree. ``agreement`` is, per field, the share of
    pages where both passes gave the same clean value (both empty counts).
    """
    files = pages(folder)

    def both(path: Path) -> tuple[dict, dict]:
        return (
            tag_page(path, schema, model, cache, reverse=False),
            tag_page(path, schema, model, cache, reverse=True),
        )

    with ThreadPoolExecutor(workers) as pool:
        answers = list(pool.map(both, files))
    tags: dict[str, dict[str, str | None]] = {}
    same: Counter = Counter()
    for path, (first, second) in zip(files, answers, strict=True):
        values: dict[str, str | None] = {}
        for field in schema.fields:
            a = clean_value(field, first.get(field.name))
            b = clean_value(field, second.get(field.name))
            same[field.name] += a == b
            values[field.name] = a if a == b else None
        tags[str(path.relative_to(folder))] = values
    total = max(1, len(files))
    agreement = {
        field.name: round(same[field.name] / total, 3) for field in schema.fields
    }
    return tags, agreement


def coverage(schema: Schema, tags: dict) -> dict:
    """Per field: pages tagged, distinct values and the most common values."""
    report = {}
    for field in schema.fields:
        filled = [p[field.name] for p in tags.values() if p.get(field.name)]
        counts = Counter(filled)
        report[field.name] = {
            "pages": len(tags),
            "tagged": len(filled),
            "distinct": len(counts),
            "top": counts.most_common(8),
        }
    return report


REVIEW_HEADER = """\
Review of the derived page tags. For each row, open the page and answer
correct: yes if the value fits the page, no if it is wrong or missing.
"""


def review_rows(tags: dict, fields: list[str], seed: int = 0) -> list[dict]:
    """Up to ``REVIEW_PAGES`` pages per field for a person to check."""
    rng = random.Random(seed)
    rows = []
    names = sorted(tags)
    for field in fields:
        for page in sorted(rng.sample(names, min(REVIEW_PAGES, len(names)))):
            rows.append(
                {
                    "field": field,
                    "page": page,
                    "value": tags[page].get(field) or "",
                    "correct": None,
                }
            )
    return rows


def apply_tags(folder: Path, tags: dict, keep: list[str]) -> int:
    """Write the kept fields under ``retrieval`` in each page.

    The same fields are removed from a legacy ``llm`` block, and the block once
    it is empty. Returns the number of pages changed.
    """
    changed = 0
    for name, values in tags.items():
        path = folder / name
        text = path.read_text(encoding="utf-8")
        current = read_front_matter(text).get(NAMESPACE)
        block = dict(current) if isinstance(current, dict) else {}
        for field in keep:
            block.pop(field, None)
            if values.get(field):
                block[field] = values[field]
        updated = set_front_matter_key(text, NAMESPACE, block or None)
        legacy = read_front_matter(updated).get(LEGACY_NAMESPACE)
        if isinstance(legacy, dict):
            rest = {k: v for k, v in legacy.items() if k not in keep}
            if rest != legacy:
                updated = set_front_matter_key(updated, LEGACY_NAMESPACE, rest or None)
        if updated != text:
            path.write_text(updated, encoding="utf-8")
            changed += 1
    return changed


def dev_queries(work: Path) -> list[str]:
    """The dev split of the validation set in ``work``, if there is one."""
    path = work / "validation.json"
    if not path.exists():
        return []
    items = read_json(path).get("items", [])
    return [item["text"] for item in items if item.get("split") == "dev"]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("step", choices=["discover", "tag", "apply"])
    parser.add_argument("folder", type=Path)
    parser.add_argument("--work", type=Path, default=Path("tmp/kb"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--region", default=DEFAULT_REGION)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--min-agreement", type=float, default=MIN_AGREEMENT)
    parser.add_argument("--keep", nargs="+", default=None, help="fields to apply")
    args = parser.parse_args(argv)
    schema_file = args.work / "schema.json"
    tags_file = args.work / "page-tags.json"
    if args.step == "discover":
        model = Model(args.model, args.region)
        queries = dev_queries(args.work)
        schema = discover(args.folder, model, queries, args.seed)
        write_json(
            schema_file,
            {
                **schema.model_dump(),
                "dev_queries_shown": min(len(queries), MAX_QUERIES),
                "usage": model.usage.report(),
            },
        )
        print(schema.describe())
        print(f"rejected: {[r.get('name') for r in schema.rejected]}", file=sys.stderr)
        return 0
    stored = read_json(schema_file)
    schema = Schema.model_validate(
        {k: v for k, v in stored.items() if k in Schema.model_fields}
    )
    if args.step == "tag":
        model = Model(schema.model, args.region)
        cache = AnswerCache(args.work / "cache" / "tags", model.usage)
        tags, agreement = tag_folder(args.folder, schema, model, cache)
        kept = [f for f, share in agreement.items() if share >= args.min_agreement]
        write_json(
            tags_file,
            {
                "agreement": agreement,
                "kept": kept,
                "coverage": coverage(schema, tags),
                "usage": model.usage.report(),
                "tags": tags,
            },
        )
        write_sheet(
            args.work / "review.yaml",
            review_rows(tags, kept, args.seed),
            REVIEW_HEADER,
            {"correct": "yes or no"},
        )
        print({"agreement": agreement, "kept": kept, "usage": model.usage.report()})
        return 0
    stored_tags = read_json(tags_file)
    keep = args.keep if args.keep is not None else stored_tags["kept"]
    unknown = set(keep) - {field.name for field in schema.fields}
    if unknown:
        parser.error(f"not in the schema: {sorted(unknown)}")
    changed = apply_tags(args.folder, stored_tags["tags"], keep)
    print(f"{changed} pages changed", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

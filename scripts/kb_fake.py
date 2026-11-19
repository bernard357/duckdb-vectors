"""A deterministic stand-in for the language model of the curation scripts.

Pass ``--model fake`` to any ``kb_*`` script, or ``KB_ARGS="--model fake"`` to
its ``make`` target, to run the whole curation path offline, at no cost: every
prompt gets a well-formed answer built from the text it is given. The answers
are not meant to be good, only shaped like real ones, so the path from the
validation set to the evaluation report can be checked end to end without AWS.
"""

import json
import re
from collections import Counter

FAKE_MODEL = "fake"
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9-]{3,}")
_NAME = re.compile(r"\b[A-Z][a-z]{3,}\b")
_FIELD = re.compile(r"^- ([A-Za-z_][A-Za-z0-9_]*): .* Allowed: (.*)$", re.MULTILINE)
_STOP = {"this", "that", "with", "from", "have", "will", "what", "which", "when"}


def _words(text: str) -> list[str]:
    return [w for w in _WORD.findall(text) if w.lower() not in _STOP]


def _between(text: str, start: str, end: str | None = None) -> str:
    tail = text.split(start, 1)[1] if start in text else text
    return tail.split(end, 1)[0] if end and end in tail else tail


def _main_name(text: str) -> str | None:
    names = Counter(_NAME.findall(text))
    return names.most_common(1)[0][0] if names else None


def fake_invoke(system: str, blocks: list[dict], max_tokens: int) -> str:
    """Answer each curation prompt in the shape it asks for."""
    text = "\n".join(block.get("text", "") for block in blocks)
    if "situate this chunk" in text:
        page = _main_name(_between(text, "<document>", "</document>")) or "the page"
        chunk = _words(_between(text, "<chunk>", "</chunk>"))[:4]
        return f"Part of the {page} page, about {' '.join(chunk)}."
    if "Propose at most" in text:
        return json.dumps(
            {
                "fields": [
                    {
                        "name": "subject",
                        "description": "The main named subject of the page.",
                        "values": "free",
                        "example_queries": [],
                        "lost_context": "which subject a passage is about",
                    }
                ],
                "rejected": [],
            }
        )
    if "Schema (field" in text:
        page = _between(text, "Page:")
        name = _main_name(page)
        answer: dict[str, str | None] = {}
        for field, allowed in _FIELD.findall(text):
            if allowed.strip() == "free":
                answer[field] = name
            else:
                values = [v.strip() for v in allowed.split(",")]
                answer[field] = next((v for v in values if v in page), None)
        return json.dumps(answer)
    if "reference answer taken from the passage" in text:
        words = _words(_between(text, "Passage:"))
        asked = re.search(r"Write (\d+) ", text)
        count = int(asked.group(1)) if asked else 2
        items = []
        for index in range(min(count, max(1, len(words) // 6))):
            pair = words[index * 3 : index * 3 + 2] or ["it"]
            items.append(
                {
                    "question": f"What is said about {' and '.join(pair)}?",
                    "answer": " ".join(words[index * 3 : index * 3 + 8]),
                }
            )
        return json.dumps({"items": items})
    if "passage A answers and passage B does not" in text:
        page = _main_name(_between(text, "Passage A", "Passage B")) or "this page"
        words = _words(_between(text, "Passage A:"))[:2] or ["it"]
        return json.dumps(
            {
                "question": f"For {page}, what about {' '.join(words)}?",
                "answer": " ".join(words),
            }
        )
    if "Grade the passage" in text:
        question = set(
            w.lower() for w in _words(_between(text, "Question:", "Reference answer"))
        )
        passage = set(w.lower() for w in _words(_between(text, "Candidate passage")))
        shared = len(question & passage)
        grade = 2 if shared >= 3 else 1 if shared == 2 else 0
        return json.dumps({"grade": grade, "reason": f"{shared} shared words"})
    raise ValueError(f"the fake model does not know this prompt: {text[:80]!r}")

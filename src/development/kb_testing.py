"""What the tests of the curation scripts share: a scripted model and a small set.

The scripted model answers each curation prompt from the text it gets, in the
shape the prompt asks for, with answers tuned to the pages of
``fixtures/tagged/`` (products Beacon and Cedar, a field ``mood`` the two
tagging passes disagree on, questions that copy their passage). Unlike
:mod:`development.fake_model`, which only has to keep the scripts running, it
lets the tests assert on what the scripts do with each answer.
"""

import json

from development.curation import AnswerCache, Model, chunk_folder
from development.paths import FIXTURES
from scripts import kb_validation

TAGGED = FIXTURES / "tagged"
HASHING = "hashing"


def fake_invoke(system: str, blocks: list[dict], max_tokens: int) -> str:
    """Answers each prompt in the shape it asks for, from the text it gets."""
    text = "\n".join(block.get("text", "") for block in blocks)
    if "situate this chunk" in text:
        chunk = text.split("<chunk>")[1].split("</chunk>")[0].split()
        return "Context about " + " ".join(chunk[:3])
    if "Propose at most" in text:
        return json.dumps(
            {
                "fields": [
                    {
                        "name": "product",
                        "description": "The product.",
                        "values": ["Beacon", "Cedar", "Other"],
                        "example_queries": [],
                        "lost_context": "procedures",
                    },
                    {
                        "name": "mood",
                        "description": "Unstable.",
                        "values": "free",
                    },
                ],
                "rejected": [{"name": "title", "reason": "unique"}],
            }
        )
    if "Schema (field" in text:
        product = "Cedar" if "Cedar" in text else "Beacon" if "Beacon" in text else None
        reverse = text.index("- mood") < text.index("- product")
        return json.dumps({"product": product, "mood": "calm" if reverse else "busy"})
    if "reference answer taken from the passage" in text:
        words = text.split("Passage:")[1].split()
        return json.dumps(
            {
                "items": [
                    {"question": f"what about {words[0]} please", "answer": "a"},
                    {"question": " ".join(words[:8]), "answer": "copied"},
                ]
            }
        )
    if "passage A answers and passage B does not" in text:
        words = text.split("Passage A:")[1].split()
        return json.dumps({"question": f"which {words[1]} applies", "answer": "x"})
    if "Grade the passage" in text:
        question = text.split("Question:")[1].split("Reference answer")[0]
        passage = text.split("Candidate passage")[1]
        shared = set(question.lower().split()) & set(passage.lower().split())
        return json.dumps({"grade": 2 if len(shared) > 2 else 0, "reason": ""})
    raise AssertionError(f"unexpected prompt: {text[:80]}")


def fake_model() -> Model:
    return Model("fake", invoke=fake_invoke)


def small_validation_set(kb, tmp_path):
    """A validation set, chunks, the scripted model and a cache over ``kb``."""
    model = fake_model()
    cache = AnswerCache(tmp_path / "vcache", model.usage)
    validation = kb_validation.ValidationSet(corpus="tagged")
    chunks = chunk_folder(kb)
    for chunk in chunks:  # fixture chunks are short; make them eligible
        chunk.text = (chunk.text + " ") * 12
    return validation, chunks, model, cache

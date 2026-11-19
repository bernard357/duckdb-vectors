"""Shared curation helpers (src/development/curation.py): prompts, front matter."""

import pytest

from development.curation import (
    load_prompt,
    read_front_matter,
    set_front_matter_key,
)
from development.paths import PROMPTS

ALL_PROMPTS = [
    ("contextual-header", 1),
    ("schema-discovery", 2),
    ("tagging", 2),
    ("query-local", 1),
    ("query-contextual", 1),
    ("relevance-judge", 1),
]


@pytest.mark.parametrize(("name", "version"), ALL_PROMPTS)
def test_prompts_declare_exactly_the_inputs_they_use(name, version):
    prompt = load_prompt(name, version)
    assert (PROMPTS / f"{name}.v{version}.md").exists()
    assert prompt.name == name
    assert prompt.placeholders() == set(prompt.inputs)
    assert prompt.system


def test_leakage_guard_of_the_prompts():
    for name in ("query-local", "query-contextual", "relevance-judge"):
        prompt = load_prompt(name)
        assert prompt.sees_tags is False
        assert not {"tags", "schema", "metadata", "context"} & set(prompt.inputs)
    for name, version in (("tagging", 2), ("contextual-header", 1)):
        assert load_prompt(name, version).sees_queries is False
    # Schema discovery sees dev queries by design; the script passes only those.
    assert load_prompt("schema-discovery", 2).sees_queries is True


def test_render_requires_every_input():
    prompt = load_prompt("query-local")
    assert "Refunds" in prompt.render(passage="Refunds take 30 days.", count="2")
    with pytest.raises(ValueError, match="missing inputs"):
        prompt.render(passage="x")


def test_set_front_matter_key_rewrites_only_that_key():
    page = "---\ntitle: A\nretrieval:\n  old: 1\nowner: bob\n---\n# A\n\nBody.\n"
    updated = set_front_matter_key(page, "retrieval", {"product": "Cedar"})
    assert read_front_matter(updated) == {
        "title": "A",
        "owner": "bob",
        "retrieval": {"product": "Cedar"},
    }
    assert updated.endswith("# A\n\nBody.\n")
    assert read_front_matter(set_front_matter_key(updated, "retrieval", None)) == {
        "title": "A",
        "owner": "bob",
    }
    assert set_front_matter_key("# A\n", "k", 1).startswith("---\nk: 1\n---\n")

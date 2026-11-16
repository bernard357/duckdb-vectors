# Tagged retrieval guide

How knowledge managers choose, control and evaluate the tags that `duckdb-vectors` adds to chunks before embedding them.

> __Draft.__ This page is written ahead of the code, from the design in the issue that tracks the work. Setting names, the prefix layout and the `tags` report columns may still change, and the thresholds that separate effective from decorative tags are not calibrated yet. Each statement is checked against the running code in the final revision, and this note is removed then.

## What is a tag for?

Chunking removes context. A chunk that says "the refund window is 30 days" no longer says which product or which audience it belongs to, so a search for the enterprise plan can return the consumer chunk. A __tag__ is a short `label: value` pair, taken from the frontmatter of the chunk's document, that restores that context. With tagged retrieval each chunk is embedded as its tags followed by its text:

```text
title: Refund policy
product: Enterprise plan
audience: finance, support

The refund window is 30 days from the invoice date.
```

Only the embedded text changes. The chunk stored in the index, and the text returned to the caller, stay the raw chunk. The method is a deterministic variant of [Contextual Retrieval](https://www.anthropic.com/news/contextual-retrieval): no language model runs while you index, so indexing stays offline and repeatable. The [research note](tagged-retrieval-research) lists what published work says and which hypotheses the benchmark tests.

A tag helps when it separates the document from its neighbours: a product name in a corpus of many products, an audience, a lifecycle stage. A tag that every document shares, or that is unique to each document, adds noise.

## How to turn tags on?

Tagging is off by default, and an index built without it is the same as before. Declare the tags as an ordered list of `label: $.path` pairs, where the path is a JSONPath expression evaluated over the document's frontmatter:

```shell
$ duckdb-vectors index ./docs --recursive \
    --tag-fields "title: $.title" \
    --tag-fields "product: $.product" \
    --tag-fields "audience: $.audience[*]"
```

From Python, pass the same list as `tag_fields`. From the environment, separate the pairs with semicolons:

```shell
$ export DUCKDB_VECTORS_TAG_FIELDS="title: $.title; product: $.product; audience: $.audience[*]"
```

The order of the list is the order of the lines in the prefix. A tag whose path finds nothing in a document is left out of that document's prefix. The `report` verb shows the active projection.

Changing the list, or a frontmatter value, re-embeds only the chunks whose prefix changed. The embedded text, prefix included, is what the content fingerprint covers.

## Which JSONPath expressions work?

`duckdb-vectors` evaluates paths with DuckDB's own JSON engine, tested on DuckDB 2.0. It supports the common subset of JSONPath, not the whole of RFC 9535.

| Frontmatter shape | Path | Result |
| --- | --- | --- |
| Top-level key | `$.title` | The value |
| Nested key | `$.product.name` | The value |
| Array | `$.audience[*]` | Every item, joined with commas |
| One array item | `$.audience[0]`, `$.audience[#-1]` | First and last item |
| Array of objects | `$.authors[*].name` | Every `name`, joined with commas |
| Nested arrays | `$.matrix[*][*]` | Flattened, joined with commas |
| Key with a hyphen or a space | `$."my-key"` | The value |
| Any depth | `$..name` | Every `name` below the root |
| Missing key | `$.missing` | No tag for that document |

Dates are written in ISO format. Filter expressions such as `$.authors[?(@.name=='x')]` and bracket-quoted keys such as `$['title']` are rejected by DuckDB. Use the quoted dot form instead.

## How to read the tags report?

```shell
$ duckdb-vectors tags --db vectors.duckdb
```

The report has one row per declared tag, computed over the whole corpus:

| Column | What it tells you |
| --- | --- |
| Coverage | Share of documents that carry the tag. Low coverage means most chunks get no help from it. |
| Cardinality | Number of distinct values. |
| Entropy | How evenly the values are spread, from 0 (one value everywhere) to 1 (every value equally common). |
| Documents per value | How many documents share a value. One means the tag is an identifier. |
| Separation lift | How much the tag widens the gap between chunks of the same document and chunks of other documents, as a mean similarity margin and as Cohen's d, with and without the tag. |
| Verdict | `effective` or `decorative`. |

A tag is __decorative__ when its coverage is low, when its entropy is close to 0 (a constant), when its entropy is close to 1 and each value belongs to one document (an identifier), or when its separation lift is negligible. Every other tag is __effective__. The numeric limits are set from benchmark results and are documented here once they are calibrated.

The report is a statement about the corpus, not about your queries. It tells you which tags can help. Whether they do for your questions needs a test: run your real queries with and without the tag.

## How long can the prefix be?

The prefix has no length cap, because truncating it would silently drop a tag you chose. The limit that matters is the embedding model's input window, and models cut input beyond it. With fastembed 0.9.1 every model cuts at 512 tokens, the default static model included, and the two paraphrase-multilingual models were trained on 128. A long prefix pushes chunk text out of the window. The `tags` report warns when prefix plus chunk exceeds the window of the active model.

Keep the prefix to the few tags that separate documents, and put the most important one first.

## How to iterate on tag selection?

1. Declare the tags you think matter, including doubtful ones.
2. Index, then run `duckdb-vectors tags`.
3. Remove tags marked decorative, or fix the frontmatter behind them. A sparse tag may only need filling in on more pages.
4. Re-index. Only chunks whose prefix changed are re-embedded.
5. Run your own questions before and after and compare the hits.

## How to control the tag vocabulary?

Tags only separate documents if the same thing is always written the same way. `Enterprise`, `enterprise plan` and `Ent.` count as three values and split a group that should be one. Agree on a list of allowed values for each tag, check frontmatter against it in review, and prefer a short fixed list over free text. If a language model proposes tags for you, keep its output in its own namespace, such as `$.llm.product`, and compare it with the native tags before you trust it.

## Follow-up

- [Tagged retrieval research note](tagged-retrieval-research) - the evidence and hypotheses behind the feature.
- [User guide](duckdb-vectors-guide) - indexing and search reference.

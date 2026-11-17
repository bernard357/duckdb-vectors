# Tagged retrieval guide

How knowledge managers choose, control and evaluate the tags that `duckdb-vectors` adds to chunks before embedding them.

- [What is a tag for?](#what-is-a-tag-for)
- [How to turn tags on?](#how-to-turn-tags-on)
- [Which JSONPath expressions work?](#which-jsonpath-expressions-work)
- [How to read the tags report?](#how-to-read-the-tags-report)
- [How long can the prefix be?](#how-long-can-the-prefix-be)
- [How to iterate on tag selection?](#how-to-iterate-on-tag-selection)
- [How to control the tag vocabulary?](#how-to-control-the-tag-vocabulary)

## What is a tag for?

Chunking removes context. A chunk that says "the refund window is 30 days" no longer says which product or which audience it belongs to, so a search for the enterprise plan can return the consumer chunk. A __tag__ is a short `label: value` pair, taken from the frontmatter of the chunk's document, that restores that context. With tagged retrieval each chunk is embedded as its tags followed by its text:

```text
product: Atlas
audience: finance, support

The refund window is 30 days from the invoice date.
```

Only the embedded text changes. The chunk stored in the index, and the text returned to the caller, stay the raw chunk. The method is a deterministic variant of [Contextual Retrieval](https://www.anthropic.com/news/contextual-retrieval): no language model runs while you index, so indexing stays offline and repeatable. The [research note](tagged-retrieval-research) lists what published work says, and the [performance workbook](performance-workbook#how-much-do-tags-improve-retrieval) gives the measured results.

A tag helps when its value is something a searcher says or something that separates chunks the searcher would confuse: a product, a company, a news source. A tag that every document shares adds noise, because it separates nothing. A tag shared by several documents that have something in common is what you want. A tag with a different value on every document is not harmful by itself: a title can match words in a query, while an opaque code such as `REF-001` cannot.

## How to turn tags on?

Tagging is off by default, and an index built without it is the same as before. Declare the tags as an ordered list of `label: $.path` pairs, where the path is a JSONPath expression evaluated over the document's frontmatter:

```shell
$ duckdb-vectors index ./docs --recursive \
    --tag-fields "product: $.product.name" \
    --tag-fields "audience: $.audience[*]"
```

From Python, pass the same list as `tag_fields` to `Settings`. From the environment, separate the pairs with semicolons:

```shell
$ export DUCKDB_VECTORS_TAG_FIELDS="product: $.product.name; audience: $.audience[*]"
```

```python
from duckdb_vectors import VectorStore, index_folder
from duckdb_vectors.config import Settings

settings = Settings(
    db_path="vectors.duckdb",
    tag_fields=["product: $.product.name", "audience: $.audience[*]"],
)
store = VectorStore(settings=settings)
index_folder(store, "docs", recursive=True)
store.close()
```

The order of the list is the order of the lines in the prefix. A tag whose path finds nothing in a document is left out of that document's prefix. Changing the list, or a frontmatter value, re-embeds only the chunks whose prefix changed. `add_texts` and `put_vectors` tag too, from the metadata you pass.

By default the tags only change the embedding. Add `--tag-bm25` (or set `DUCKDB_VECTORS_TAG_BM25=true`) to also match them in the exact-token keyword search, so a query that names a product finds chunks whose text never says it:

```shell
$ duckdb-vectors index ./docs --recursive --tag-fields "product: $.product.name" --tag-bm25
```

The keyword index is built by the process that indexes, and a search follows whatever index the file holds. Indexing again with the other `--tag-bm25` setting rebuilds it, even when no page changed. The `report` verb shows the tags that the current settings declare:

```shell
$ DUCKDB_VECTORS_TAG_FIELDS="product: $.product.name" duckdb-vectors report
```

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

Dates are ISO text. PDF files expose their document information as `$.title`, `$.author`, `$.subject` and `$.keywords`, when the file declares them, so the same tag paths work on PDFs and Markdown. Filter expressions such as `$.authors[?(@.name=='x')]` and bracket-quoted keys such as `$['title']` are rejected by DuckDB. Use the quoted dot form instead.

## How to read the tags report?

```shell
$ duckdb-vectors tags --tag-fields "product: $.product.name" --tag-fields "kind: $.kind" \
    --queries my-queries.txt
```

`--queries` takes a text file with one real or realistic query per line. The report has one entry per declared tag, computed over the whole index:

| Field | What it tells you |
| --- | --- |
| `coverage` | Share of documents that carry the tag. Low coverage means most chunks get no help from it. |
| `cardinality` | Number of distinct values. |
| `entropy` | How evenly the values are spread, from 0 (one value everywhere) to 1 (every value equally common). |
| `documents_per_value` | How many documents share a value. About one adds a note: a title helps if queries use titles, an opaque code does not. |
| `query_mention` | With `--queries`: share of the queries that contain at least one of the tag's values as whole words. |
| `verdict`, `reasons` | `effective` or `decorative`, and why. |
| `margin_*`, `d_*`, `value_*` | How much the tag raises the similarity of chunks of one document (`margin`, `d`) or of chunks that share its value (`value`) over other chunks, with and without the tag. Reported, not used for the verdict. |

A tag is __decorative__ when its coverage is below 50%, when it has a single value, or, with queries, when fewer than 20% of the queries contain one of its values. Every other tag is __effective__. Without `--queries` only sparse and constant tags are screened out, and each tag carries a note that says so.

The queries rule exists because the separation measures did not predict benefit. Over 17 tags in four corpora, the rank correlation between a tag's separation lift and the retrieval gain it brought was close to zero, and the company name and the news source that helped most scored as decorative. The share of queries that mention a tag's values correlated at 0.62. The 20% threshold was fitted to those same 17 tags, so treat it as a starting point. The [performance workbook](performance-workbook#which-tags-help) has the numbers.

The report cannot tell you whether a tag helps your searchers. It tells you which tags to try and which to drop first.

## How long can the prefix be?

The prefix has no length cap, because truncating it would silently drop a tag you chose. The limit that matters is the embedding model's input window, and models cut input beyond it. With fastembed 0.9.1 every model cuts at 512 tokens, the default static model included, and the two paraphrase-multilingual models were trained on 128. A long prefix pushes the end of the chunk out of the window. The report warns when prefix plus chunk probably exceeds the window of the active model, estimated at four characters per token.

Keep the prefix to the few tags that matter. The benchmark found no reliable difference between putting the tags before or after the chunk text.

## How to iterate on tag selection?

1. Declare the tags you think matter, including doubtful ones.
2. Index, then run `duckdb-vectors tags --queries my-queries.txt`.
3. Remove tags marked decorative, or fix the frontmatter behind them. A sparse tag may only need filling in on more pages.
4. Re-index. Only chunks whose prefix changed are re-embedded.
5. Run your own questions before and after and compare the hits.

## How to control the tag vocabulary?

Tags only group documents if the same thing is always written the same way. `Enterprise`, `enterprise plan` and `Ent.` count as three values and split a group that should be one. Agree on a list of allowed values for each tag, check frontmatter against it in review, and prefer a short fixed list over free text. If a language model proposes tags for you, keep its output under its own key, such as `$.llm.product`, compare it with the native tags before you trust it, and discard answers such as "Other" or "None": a tag that says nothing tells a search nothing.

## Follow-up

- [Tagged retrieval research note](tagged-retrieval-research): the evidence and hypotheses behind the feature.
- [Performance workbook](performance-workbook): the measured results.
- [User guide](duckdb-vectors-guide): indexing and search reference.

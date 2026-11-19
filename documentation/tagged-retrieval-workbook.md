# Tagged retrieval workbook

Questions that knowledge managers ask about tagged retrieval in `duckdb-vectors`, answered with the practice first and the evidence after. The pages that apply it are the [build procedure](build-knowledge-base-procedure), the [tagging procedure](tag-knowledge-base-procedure) and the [inspection procedure](inspect-knowledge-base-procedure).

- [What is tagged retrieval?](#what-is-tagged-retrieval)
- [What is a tag for?](#what-is-a-tag-for)
- [How to turn tags on?](#how-to-turn-tags-on)
- [Which JSONPath expressions work?](#which-jsonpath-expressions-work)
- [How to give each section its own context?](#how-to-give-each-section-its-own-context)
- [Who should write the tags?](#who-should-write-the-tags)
- [How to control the tag vocabulary?](#how-to-control-the-tag-vocabulary)
- [How long can the prefix be?](#how-long-can-the-prefix-be)
- [Can tags be used in SQL queries?](#can-tags-be-used-in-sql-queries)
- [What does published work show?](#what-does-published-work-show)
- [What do we expect, and what did our runs find?](#what-do-we-expect-and-what-did-our-runs-find)

## What is tagged retrieval?

Tagged retrieval puts context back in front of a chunk before it is embedded. It is a deterministic variant of Anthropic's [Contextual Retrieval](https://www.anthropic.com/news/contextual-retrieval): the context comes from the front matter of the page, and no language model runs while you index. Indexing stays offline and repeatable.

Three terms are used consistently in the documentation:

- A __tag__ is a short `label: value` pair taken from the front matter of a page.
- A __section context__ is a sentence that situates one section in its page. It is stored as metadata of that section, and it is indexed like any other tag.
- The __prefix__ is the block of tag lines put in front of the chunk text before embedding.

## What is a tag for?

Chunking removes context. A chunk that says "the refund window is 30 days" no longer says which product or which audience it belongs to, so a search for the enterprise plan can return the consumer chunk. A tag restores that context. With tagged retrieval each chunk is embedded as its tags followed by its text:

```text
product: Atlas
audience: finance, support
The refund window is 30 days from the invoice date.
```

Only the embedded text changes. The chunk stored in the index, and the text returned to the caller, stay the raw chunk.

A tag helps when its value is something a searcher says, or something that separates chunks the searcher would confuse: a product, a company, a news source. A tag that every document shares adds noise, because it separates nothing. A tag shared by several documents that have something in common is what you want. A tag with a different value on every document does no harm by itself: a title can match words in a query, while an opaque code such as `REF-001` cannot.

## How to turn tags on?

Tagging is off by default, and an index built without it is the same as before. Declare the tags as an ordered list of `label: $.path` pairs, where the path is a JSONPath expression evaluated over the front matter of the page:

```shell
$ duckdb-vectors index ./docs --recursive \
    --tag-field "product: $.product.name" \
    --tag-field "audience: $.audience[*]"
```

From the environment, separate the pairs with semicolons:

```shell
$ export DUCKDB_VECTORS_TAG_FIELDS="product: $.product.name; audience: $.audience[*]"
```

From Python, pass the same list as `tag_fields` to `Settings`:

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

The order of the list is the order of the lines in the prefix. A tag whose path finds nothing in a document is left out of the prefix of that document. Changing the list, or a front matter value, re-embeds only the chunks whose prefix changed. `add_texts` and `put_vectors` tag too, from the metadata you pass.

By default the tags only change the embedding. Add `--tag-bm25`, or set `DUCKDB_VECTORS_TAG_BM25=true`, to also match them in the exact-token keyword search, so that a query that names a product finds chunks whose text never says it:

```shell
$ duckdb-vectors index ./docs --recursive \
    --tag-field "product: $.product.name" --tag-bm25
```

The keyword index is built by the process that indexes, and a search follows whatever index the file holds. Indexing again with the other `--tag-bm25` setting rebuilds it, even when no page changed.

## Which JSONPath expressions work?

`duckdb-vectors` evaluates paths with the JSON engine of DuckDB, tested on DuckDB 2.0. It supports the common subset of JSONPath, not the whole of [RFC 9535](https://www.rfc-editor.org/rfc/rfc9535).

| Front matter shape | Path | Result |
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

Dates are ISO text. PDF files expose their document information as `$.title`, `$.author`, `$.subject` and `$.keywords`, when the file declares them, so the same paths work on PDF and Markdown files. Filter expressions such as `$.authors[?(@.name=='x')]` and bracket-quoted keys such as `$['title']` are rejected by DuckDB. Use the quoted dot form instead.

## How to give each section its own context?

Front matter describes a whole page, so its tags are the same for every chunk of the page. A Markdown page can also carry metadata for one section only, under the reserved `sections` key. Each entry is keyed by the section path that the chunker reports in the `section_path` of each chunk:

```yaml
---
title: Deployment runbook
sections:
  deployment-runbook/rollback:
    retrieval:
      context: How to roll back a failed Cedar release, after the canary step.
---
```

The chunker merges each entry into the chunks of that section, so `$.retrieval.context` is a tag like any other:

```shell
$ duckdb-vectors index ./docs --recursive \
    --tag-field "context: $.retrieval.context" --tag-bm25
```

An entry whose path matches no section is logged as a warning, so a renamed heading does not fail silently. Writing a context like this for every section, with a language model that reads each page once, is Anthropic's Contextual Retrieval moved upstream of indexing. The [tagging procedure](tag-knowledge-base-procedure) does it, and measures the result on your own questions.

## Who should write the tags?

People write the best tags, because they know the words that searchers use. Use the front matter that already exists, and fill it in where it is sparse.

A language model can derive more tags when pages have little front matter. Keep what it writes under its own key, such as `$.retrieval.product`, so that derived values never mix with tags written by people. The [tagging procedure](tag-knowledge-base-procedure#step-3-derive-page-tags) proposes the fields, tags every page twice to check that the model agrees with itself, and asks a person to review a sample. Agreement shows that a tag is consistent. It does not show that the tag helps, and only a run on your own questions does.

## How to control the tag vocabulary?

Tags only group documents when the same thing is always written the same way. `Enterprise`, `enterprise plan` and `Ent.` count as three values and split a group that should be one. Agree on a list of allowed values for each tag, check the front matter against it in review, and prefer a short fixed list over free text.

If a language model proposes tags, compare them with the native tags before you trust them, and discard answers such as "Other" or "None": a tag that says nothing tells a search nothing. The [inspection procedure](inspect-knowledge-base-procedure#step-3-review-the-values-of-each-tag) lists the values of each tag, so you can spot variants.

## How long can the prefix be?

The prefix has no length cap, because truncating it would silently drop a tag you chose. The limit that matters is the input window of the embedding model, and models cut input beyond it. With fastembed 0.9.1 every model cuts at 512 tokens, the default static model included, and the two paraphrase-multilingual models were trained on 128. A long prefix pushes the end of the chunk out of the window.

The `tags` report warns when prefix plus chunk probably exceeds the window of the active model, estimated at four characters per token. Keep the prefix to the few tags that matter. The benchmark found no reliable difference between putting the tags before or after the chunk text, except for transformers, as the last section explains.

## Can tags be used in SQL queries?

Yes. The front matter of a page is also stored as JSON in the `metadata` column of each of its chunks, and section metadata is merged into the chunks of that section. A plain `SELECT` can filter on any of it, whether or not the tag is embedded:

```sql
SELECT source, title
FROM chunks
WHERE json_extract_string(metadata, '$.kind') = 'policy';
```

The [user guide](duckdb-vectors-guide) shows how to inject such a filter into a search, or into the ranking itself.

## What does published work show?

Four sources frame the practice. Every figure below was read from the cited source on 2026-11-16.

__Contextual Retrieval.__ [Anthropic](https://www.anthropic.com/news/contextual-retrieval) prepends a chunk-specific explanation, written by Claude 3 Haiku, to each chunk before embedding it and before building the BM25 index. The context is usually 50 to 100 tokens. Results are the failure rate at top 20 (1 minus recall@20), averaged over codebases, fiction, ArXiv papers and science papers, with Gemini Text 004 as the best embedder:

| Configuration | Failure rate at top 20 | Reduction |
| --- | --- | --- |
| Baseline (embeddings and BM25) | 5.7% | |
| Contextual embeddings | 3.7% | 35% |
| Contextual embeddings and contextual BM25 | 2.9% | 49% |
| Both, plus a reranker | 1.9% | 67% |

Three details matter. The cost is one language model call per chunk, which prompt caching brings to about $1.02 per million document tokens under the assumptions of the post. The post tried 5, 10 and 20 chunks and found 20 the best, which is the basis for our default top-K of 20. And the authors report that generic document summaries gave very limited gains, so the benefit comes from chunk-specific disambiguation, not from any text about the document. The [cookbook](https://github.com/anthropics/claude-cookbooks/tree/main/capabilities/contextual-embeddings) ships the code set that we reuse: 90 files from 9 repositories, 737 chunks, and 248 queries with golden chunks.

__Metadata as text.__ [Yousuf et al., ECIR 2026](https://arxiv.org/abs/2601.11863) compare metadata flattened into the embedded text as a prefix or a suffix, a dual encoder, late fusion, and metadata-aware query reformulation, on SEC filings. They report that prefixing and the unified embedding consistently beat plain-text baselines. Their embedding-space analysis attributes the gain to higher cohesion within a document, less confusion between documents, and a wider gap between relevant and irrelevant chunks. They tested one corpus type, so how far the result carries is an open question.

__Free structure against a language model call.__ Two preprints from single teams, without independent reproduction as far as we know, compare a model call per chunk with structure that costs nothing:

- [Upgrade the Retriever First](https://arxiv.org/abs/2603.23533) (arXiv v4, October 2026, a confirmatory study with a plan published before indexing, 951 Qasper and 235 FreshStack questions, eight retrievers). Under a hybrid retriever, enrichment by a 7B model beats a free section-path prefix by 5.5 recall points on Qasper, but the gain on FreshStack (2.7) does not survive the Holm adjustment. Under the two strongest retrievers, the intervals fall within the equivalence margins. Under BM25 enrichment gains 8.7 points. The authors withdrew their v1 and v2 results.
- [Structure-aware chunking with title-chain prefixes](https://arxiv.org/abs/2608.00824) reports MRR@5 rising from 0.374 to 0.463 on 1,600 queries over a production Markdown knowledge base, with zero model calls. It warns that a prefix on/off ablation can corrupt the relevance annotation, because stripping the prefix removes the signal that annotators need.

They set the bar: a deterministic prefix has to be compared with a model call, not only with no prefix.

__Other sources.__ [MultiHop-RAG](https://arxiv.org/abs/2401.15391) (Tang and Yang, 2024) has 2,556 multi-hop queries over news articles with metadata (author, category, publication date, source, title), under the ODC-BY licence. It serves as a corpus with native tags. A 2025 paper by Dadopoulos and Ladas is cited in the tracking issue, but the only match found is an SSRN paper on annual reports whose page returned HTTP 403. It stays uncited until someone confirms what it measures.

## What do we expect, and what did our runs find?

The hypotheses were fixed before the benchmark, each one falsifiable, and negative results are reported. The verdicts come from the [performance workbook](performance-workbook#how-much-do-tags-improve-retrieval), which gives every run with its intervals.

| Id | Hypothesis | Verdict |
| --- | --- | --- |
| H1 | Tags reduce failure@20 on tag-rich corpora | Holds when queries use the words the tags carry, and can fail otherwise |
| H2 | Gains are larger for small static embedders than for large transformers | Not supported |
| H3 | Tagged BM25 adds gain on top of tagged embeddings | Holds in part |
| H4 | Tags recover at least 50% of the gain of model-written contexts at near-zero cost | Not supported |
| H5 | Corpus statistics predict which tags help | Holds for the share of queries that mention a tag's values, not for embedding separation |

Two results differ from the published work. Tags placed before the chunk text moved the scores of a transformer far more than tags placed after it, in both directions. And a title-like tag, unique to each document, did no harm.

Constraints on reading the results:

- The code set has native tags only through a join on the evaluation file (repository, path).
- The `engineering-brain` corpus has one front matter key per page, `topic`, which is nearly an identifier, so its useful tags are derived by a language model and live under their own key.
- Every fastembed 0.9.1 model truncates input at 512 tokens, so a long prefix can push chunk text out of the window.

## Follow-up

- [Build a knowledge base](build-knowledge-base-procedure): the end-to-end procedure that uses tags.
- [Tag a knowledge base](tag-knowledge-base-procedure): write section contexts and page tags offline, and measure them.
- [Inspect a knowledge base](inspect-knowledge-base-procedure): review tags, their distribution, and search results.
- [Performance workbook](performance-workbook): the measured results.
- [User guide](duckdb-vectors-guide): the indexing and search reference.
- [Issue #64](https://github.com/bernard357/duckdb-vectors/issues/64): the tracking issue of this work.

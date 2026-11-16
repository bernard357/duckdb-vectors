# Tagged retrieval benchmark

Measures whether embedding frontmatter-projected tags with each chunk, or
matching them in the keyword index, changes retrieval quality. The design and
the hypotheses H1 to H5 are in
[the research note](../../documentation/tagged-retrieval-research.md).

## Run it

```shell
$ make bench-tagged
$ make bench-tagged BENCH_ARGS="--conditions baseline --backend hashing --limit-queries 20"
```

The first run downloads the Anthropic cookbook data, pinned to one commit, into
`.cache/`. Each run writes a JSON file to `results/`, named after the git SHA and
the backend, with the dirty-tree flag, the SHA-256 of the data files, every
per-query score and the comparisons with the baseline. Both folders are
ignored by git.

## Conditions

| Condition | Embedded text | Keyword index |
| --- | --- | --- |
| `baseline` | raw chunk | text and title |
| `tags_embedding` | tags and chunk | text and title |
| `tags_bm25` | raw chunk | text, title and tags |
| `tags_both` | tags and chunk | text, title and tags |

Each condition is searched vector-only and hybrid at `--top-k` 5, 10 and 20.
Pass@k is the mean share of a query's golden chunks found in the top k, as in
the cookbook. Differences from the baseline come with a 95% paired bootstrap
interval and an exact McNemar test on "every golden chunk found".

## Datasets

| Dataset | Status |
| --- | --- |
| Anthropic cookbook codebases (90 files, 737 chunks, 248 queries) | loader and runner done; uses the published chunks as they are, so results compare with the cookbook |
| RAGMATE-10K, MultiHop-RAG | not yet |
| `engineering-brain/documentation` (private) | not yet |

## Sanity check of the baseline

With the pinned data, vector-only Pass@5 and Pass@20 are 0.440 and 0.647 with the
default static model and 0.702 and 0.818 with `BAAI/bge-small-en-v1.5`. The
cookbook reports 0.809 and 0.901 for its baseline with a Voyage embedding model.
The ordering follows model quality. The numbers are not equal because the
embedders differ, so they show that the chunk mapping works, not that this
harness reproduces the cookbook.

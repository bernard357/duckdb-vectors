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
| RAGMATE-10K (25 SEC 10-K filings, 4,490 chunks, 117 usable questions) | loader and runner done (`--dataset ragmate`, needs `pip install 'duckdb-vectors[benchmarks]'`); filings are re-chunked with the dataset's own recipe |
| MultiHop-RAG | not yet |
| `engineering-brain/documentation` (private) | not yet |

Tags default to a preset per dataset (`repo`, `directory` and `extension` for the
cookbook set; `company`, `year` and `section` for RAGMATE-10K), and
`--tag-fields` replaces it.

### RAGMATE-10K data quality

The ground truth is the set of chunks cited by a RAG pipeline's generated
answer, not a human judgement, and only four company-years carry questions. Each
run prints a quality report in its JSON file: of 326 cited chunk ids, 3 carried a
stray `chunk_id:` label and were repaired, 4 were malformed and dropped, and 3 of
the 120 questions were left with no usable chunk. All 319 cited chunk texts shipped
with the ground truth are identical to the re-chunked text. The repository states
no licence, so the data is used for local benchmarking and no derived set is
published.

## Sanity check of the baseline

With the pinned data, vector-only Pass@5 and Pass@20 are 0.440 and 0.647 with the
default static model and 0.702 and 0.818 with `BAAI/bge-small-en-v1.5`. The
cookbook reports 0.809 and 0.901 for its baseline with a Voyage embedding model.
The ordering follows model quality. The numbers are not equal because the
embedders differ, so they show that the chunk mapping works, not that this
harness reproduces the cookbook.

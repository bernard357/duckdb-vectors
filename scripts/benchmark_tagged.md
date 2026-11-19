# Tagged retrieval benchmark

Measures whether embedding frontmatter-projected tags with each chunk, or
matching them in the keyword index, changes retrieval quality. The design and
the hypotheses H1 to H5 are in
[the tagged retrieval workbook](../documentation/tagged-retrieval-workbook.md).

## Run it

```shell
$ make bench-tagged
$ make bench-tagged BENCH_ARGS="--conditions baseline --backend hashing --limit-queries 20"
```

The script is `scripts/benchmark_tagged.py`. The first run downloads the
Anthropic cookbook data, pinned to one commit, into `tmp/benchmarks/cache/`. Each
run writes a JSON file to `tmp/benchmarks/results/`, named after the git SHA and
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
| MultiHop-RAG (609 news articles, 4,303 chunks, 2,255 usable questions) | loader and runner done (`--dataset multihop`); articles are cut into the package's word windows and each quoted fact is mapped to the chunks that hold it |
| `engineering-brain/documentation` (private) | retired from this runner; now curated and measured with `scripts/kb_*.py` (see below) |
| Anthropic set with LLM-written contexts | reference run for the hypothesis H4 (`--dataset anthropic-context`) |

Tags default to a preset per dataset (`repo`, `directory` and `extension` for the
cookbook set; `company`, `year` and `section` for RAGMATE-10K), and
`--tag-field` replaces it. MultiHop-RAG uses `title`, `source`, `category` and
`published`.

### RAGMATE-10K data quality

The ground truth is the set of chunks cited by a RAG pipeline's generated
answer, not a human judgement, and only four company-years carry questions. Each
run prints a quality report in its JSON file: of 326 cited chunk ids, 3 carried a
stray `chunk_id:` label and were repaired, 4 were malformed and dropped, and 3 of
the 120 questions were left with no usable chunk. All 319 cited chunk texts shipped
with the ground truth are identical to the re-chunked text. The repository states
no licence, so the data is used for local benchmarking and no derived set is
published.

### MultiHop-RAG golden-chunk mapping

The dataset gives each question's evidence as a quoted fact in an article, not as
a chunk. A fact counts as found in every chunk that contains it (whitespace
squashed), and a question is satisfied for that fact by any one of them, so the
score is the share of evidence items found. Of 6,084 evidence items, 3 are in no
chunk and are dropped, and 499 sit in two overlapping chunks. The 301
`null_query` questions have no evidence and are not used, which leaves 2,255 of
2,556. The corpus is under ODC-BY.

## Sanity check of the baseline

With the pinned data, vector-only Pass@5 and Pass@20 are 0.440 and 0.647 with the
default static model and 0.702 and 0.818 with `BAAI/bge-small-en-v1.5`. The
cookbook reports 0.809 and 0.901 for its baseline with a Voyage embedding model.
The ordering follows model quality. The numbers are not equal because the
embedders differ, so they show that the chunk mapping works, not that this
harness reproduces the cookbook.

## Knowledge-base runs (#65)

The runs that replace the first results treat every corpus as a folder of
Markdown pages, the way a knowledge manager's corpus is, and go through the
[curation scripts](../documentation/tag-knowledge-base-procedure.md).
`benchmark_kb_datasets.py` writes a public dataset out as such a folder, with its
questions as a validation set whose golden chunks are the folder's own chunk
ids, split once into dev and test:

```shell
$ .venv/bin/python -m scripts.benchmark_kb_datasets anthropic \
    --folder tmp/kb/anthropic/pages --work tmp/kb/anthropic
```

| Preparer | Pages | Questions and golden chunks |
| --- | --- | --- |
| `anthropic` | one page per code file, one section per published chunk | the cookbook's 248 questions; the converted baseline equals the published one |
| `legalbench` | privacy policies, NDAs and contracts from LegalBench-RAG (privacy_qa, contractnli, cuad; MAUD left out), 250-word sections | 194 seeded questions per subset; a section is golden when it overlaps an answer span |
| `financebench` | one page per filing, one section per PDF page, pinned to one commit | 149 of the 150 questions of the public sample; evidence pages matched by text, 188 of 189 exactly |

### Licences, sizes and sources

| Dataset | Licence | Source and size | Republished here |
| --- | --- | --- | --- |
| FinanceBench open sample | CC BY-NC 4.0 (its [dataset card](https://huggingface.co/datasets/PatronusAI/financebench)); the filings are public SEC documents | downloaded on first use from `patronus-ai/financebench` on GitHub, pinned to commit `cc39aeb`; 84 PDFs and the questions, 159 MB | no: results only |
| LegalBench-RAG | code MIT; data under its sources' terms: CUAD and ContractNLI CC BY 4.0, PrivacyQA MIT (MAUD, CC BY 4.0, is not used) | the authors' [Dropbox folder](https://github.com/zeroentropy-ai/legalbenchrag#download), downloaded by hand and unpacked into `tmp/benchmarks/cache/legalbenchrag/data`; an 86 MB archive, 88 MB unpacked | the section contexts only, in `fixtures/benchmarks/` |
| Anthropic code set | MIT (the Anthropic cookbook) | downloaded on first use from the cookbook, pinned to commit `ce8b262` | the section contexts only, in `fixtures/benchmarks/` |

The source texts are used for local benchmarking only. The Markdown pages and
validation sets derived from them stay in git-ignored work folders. The section
contexts of the two permissively licensed sets are published in
[`fixtures/benchmarks/`](../fixtures/benchmarks/README.md), with attribution, so
that their results can be reproduced with no model call. FinanceBench keeps
results only.

Section contexts then come from `make kb-context`, or from a pack with
`KB_ARGS="--from-pack fixtures/benchmarks/<corpus>-contexts.json.gz"`, and `make kb-evaluate` scores
each condition. `benchmark_kb_analyze.py` prints the tables and the pre-registered
decision, Holm-corrected over every primary test given:

```shell
$ .venv/bin/python -m scripts.benchmark_kb_analyze tmp/kb/*/results/test-*.json
```

## Results

Run `make bench-tagged` per dataset or `python -m scripts.benchmark_tagged_sweep` for the whole matrix, then
`python -m scripts.benchmark_tagged_analyze` to print the tables. The measured results and the verdict on each
hypothesis are in the
[performance workbook](../documentation/performance-workbook.md).

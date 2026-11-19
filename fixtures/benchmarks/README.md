# Benchmark data

Frozen data behind the published results of tagged retrieval, so that you can
reproduce them without calling a language model. These files are a snapshot:
they change only when the published results change.

## Section contexts

Each pack is the `contexts.json` that `make kb-context` wrote for one public
corpus, gzipped, keyed by page then by `section_path`. The run's token usage is
removed; the costs are in the
[performance workbook](../../documentation/performance-workbook.md#what-does-curation-cost).

| File | Corpus | Pages | Sections | SHA-256 |
| --- | --- | --- | --- | --- |
| `anthropic-contexts.json.gz` | Anthropic's code set (Contextual Retrieval cookbook) | 90 | 737 | `85e18eb94ffe04001124402eb988823a301d3410790f5b3dfa147136b8f22639` |
| `legalbench-contexts.json.gz` | LegalBench-RAG: privacy_qa, contractnli, cuad | 232 | 7,199 | `a946cecb5fb5bcdf8d67504a84cde41adc7e12b1c3fbda64ccdba9d5717749a4` |

The contexts were written by Claude Haiku 4.5
(`eu.anthropic.claude-haiku-4-5-20251001-v1:0` on Amazon Bedrock, temperature 0)
with the prompt `fixtures/prompts/contextual-header.v1.md`, which is Anthropic's
prompt word for word.

Apply a pack to the pages that `scripts/benchmark_kb_datasets.py` prepares for
the same corpus. The script refuses a pack that names a page or a section that
the folder does not have:

```shell
$ .venv/bin/python -m scripts.benchmark_kb_datasets anthropic \
    --folder tmp/kb/anthropic/pages --work tmp/kb/anthropic
$ make kb-context KB=tmp/kb/anthropic/pages WORK=tmp/kb/anthropic \
    KB_ARGS="--from-pack fixtures/benchmarks/anthropic-contexts.json.gz"
```

## Licences and attribution

The contexts are derived from the following datasets and are redistributed
under their terms. The source texts themselves are not included.

- Anthropic's code set, from the
  [Anthropic cookbook](https://github.com/anthropics/anthropic-cookbook) at commit
  `ce8b2629efe8b24048b127b4c79af1e9cbab93d4`: MIT.
- [LegalBench-RAG](https://github.com/zeroentropy-ai/legalbenchrag), Pipitone and
  Houir Alami, 2024: code MIT; data under its sources' terms, CUAD and
  ContractNLI CC BY 4.0, PrivacyQA MIT.

FinanceBench (CC BY-NC 4.0) and the private engineering pages have no pack:
their results are published, their contexts are not.

These files are not part of the Python package or of its source distribution.

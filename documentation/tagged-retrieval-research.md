# Tagged retrieval research note

What published work says about prefixing chunks with document-level context before embedding, and what that implies for __tagged retrieval__ in `duckdb-vectors`: each chunk is embedded with a prefix built from tags projected out of its document's frontmatter. This note fixes the hypotheses the benchmark must confirm or refute. Every figure below was read from the cited source on 2026-11-16; none comes from our own runs yet.

## What does Contextual Retrieval show?

[Anthropic's Contextual Retrieval](https://www.anthropic.com/news/contextual-retrieval) prepends a chunk-specific explanation, written by Claude 3 Haiku, to each chunk before embedding it (Contextual Embeddings) and before building the BM25 index (Contextual BM25). The generated context is usually 50 to 100 tokens. Results are reported as top-20 retrieval failure rate (1 minus recall@20), averaged over codebases, fiction, ArXiv papers and science papers, with Gemini Text 004 as the best embedder:

| Configuration | Failure rate at top 20 | Reduction |
| --- | --- | --- |
| Baseline (embeddings and BM25) | 5.7% | |
| Contextual Embeddings | 3.7% | 35% |
| Contextual Embeddings and Contextual BM25 | 2.9% | 49% |
| Both, plus a reranker | 1.9% | 67% |

Three details matter here. The cost is one LLM call per chunk, which prompt caching brings to about $1.02 per million document tokens under the post's assumptions. The post tried 5, 10 and 20 chunks and found 20 the best of the three, which is the basis for our top-K default of 20. And the authors report that adding __generic document summaries__ to chunks gave very limited gains, so the benefit comes from chunk-specific disambiguation, not from any text about the document.

The [cookbook](https://github.com/anthropics/claude-cookbooks/tree/main/capabilities/contextual-embeddings) ships the codebase set we reuse: 90 files from 9 repositories, 737 chunks, 248 queries with golden chunks.

## What does metadata-as-text show?

[Yousuf et al., ECIR 2026](https://arxiv.org/abs/2601.11863) compare metadata flattened into the embedded text as a prefix or a suffix, a dual encoder that fuses metadata and content in one index, dual-encoder late fusion, and metadata-aware query reformulation, on SEC filings (the RAGMATE-10K dataset). They report that prefixing and the unified embedding consistently beat plain-text baselines, with the unified embedding sometimes ahead of prefixing and easier to maintain. Their embedding-space analysis attributes the gain to higher intra-document cohesion, less inter-document confusion and a wider gap between relevant and irrelevant chunks. Field-level ablations show that structural cues carry strong disambiguating signal. This is the basis for our prefix placement and for the separation measures in the `tags` report.

The authors compare fusion approaches only on one corpus type, which is regulatory filings. How far the result carries to other corpora is the open question for us.

## What argues for free structure over an LLM call?

Two recent preprints compare an LLM call per chunk with structure that costs nothing:

- [Upgrade the Retriever First](https://arxiv.org/abs/2603.23533) (arXiv v4, October 2026, confirmatory study with a plan published before indexing, 951 Qasper and 235 FreshStack questions, eight retrievers). Under a hybrid retriever, 7B enrichment beats a free section-path prefix by 5.5 recall points on Qasper, but the gain on FreshStack (2.7) does not survive Holm adjustment. Under the two strongest retrievers (qwen3-embedding and a reranker) the confidence intervals of enrichment against the free prefix fall within the equivalence margins, and under BM25 enrichment gains 8.7 points. The free prefix under either strong retriever beats hybrid enrichment by 3.4 to 6.3 points. The authors withdrew their v1 and v2 results.
- [Structure-aware chunking with title-chain prefixes](https://arxiv.org/abs/2608.00824) reports MRR@5 rising from 0.374 to 0.463 on a 1,600-query evaluation over a production Markdown knowledge base, with zero LLM calls, and warns that a prefix on/off ablation can corrupt relevance annotation, because stripping the prefix removes the signal annotators need.

Both are preprints from single teams on their own corpora, and I found no independent reproduction. They are the closest published relatives of tagged retrieval and set the bar: a deterministic prefix has to be compared with an LLM call, not only with no prefix.

## What about the other sources?

- __MultiHop-RAG__ ([Tang and Yang, 2024](https://arxiv.org/abs/2401.15391)): 2,556 multi-hop queries over news articles with metadata (author, category, publication date, source, title). It is licensed ODC-BY. It serves as a corpus with native tags.
- __Dadopoulos and Ladas, 2025__: the issue cites a 2025 paper by these authors. The only match I found is an SSRN paper on RAG benchmarking on annual reports (abstract 5257074), and the page returned HTTP 403, so I could not read it. It stays uncited in the argument until someone confirms it is the intended reference and what it measures.
- __Counter-evidence__: Anthropic's own note on generic summaries (above) is the main caution. A tag that restates what every chunk of the document already says adds noise, which is the reason for the `tags` report.

## What do we expect, and how will we know?

Each hypothesis is falsifiable and each has a verdict slot in the performance workbook. Negative results are reported.

| Id | Hypothesis | Measured by |
| --- | --- | --- |
| H1 | Tags reduce failure@20 on tag-rich corpora | Baseline versus tags in the embedding, all four datasets |
| H2 | Gains are larger for small static embedders than for large transformers | Same comparison across potion, MiniLM, e5-large, bge-large |
| H3 | Tagged BM25 adds gain on top of tagged embeddings | The four conditions, vector-only and hybrid |
| H4 | Tags recover at least 50% of the gain of LLM contextual headers at near-zero index cost | Optional cached LLM-header run on the codebase set |
| H5 | Corpus statistics (coverage, entropy, separation lift) predict which tags help | Statistics from the `tags` report against leave-one-tag-out results |

Constraints on reading the results. The codebase set has native tags only through a join on the evaluation file (repository, path). The `engineering-brain` corpus has one frontmatter key per page, `topic`, which is nearly an identifier, so its useful tags are derived with an LLM and live in a separate `llm` namespace. Under fastembed 0.9.1 every model truncates input at 512 tokens, the static default included, so a long prefix can push chunk text out of the window. The report warns about that case.

## What did the runs find?

The [performance workbook](performance-workbook#how-much-do-tags-improve-retrieval) reports every run with intervals. In short: H1 holds when queries use the words the tags carry and can fail otherwise, H2 and H4 are not supported, H3 holds in part, and H5 holds for the share of queries that mention a tag's values but not for embedding separation. Two results differ from the published work and are worth stating: tags placed before the chunk text moved a transformer's scores far more than tags placed after it, in both directions, and a title-like tag unique to each document did no harm.

## Follow-up

- [Performance workbook](performance-workbook) - where the measured results and hypothesis verdicts will land.
- [Issue tracking this work](https://github.com/bernard357/duckdb-vectors/issues/64)

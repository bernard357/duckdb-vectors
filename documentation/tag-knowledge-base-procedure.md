# Tag a knowledge base

This sub-procedure details [step 3 of the build procedure](build-knowledge-base-procedure#step-3-tag-markdown-pages-and-sections). A knowledge manager prepares a folder of Markdown pages so that `duckdb-vectors` finds more of the right passages. A language model reads each page once, offline, and writes two kinds of metadata into the pages' front matter: a short context for each section, as in Anthropic's [Contextual Retrieval](https://www.anthropic.com/news/contextual-retrieval), and a few page-level tags. A validation set, built before any of that, tells you whether either one helps on your own pages. Nothing changes at indexing or search time: the package reads the new front matter like any other tag, and no model runs while you index.

The scripts live in `scripts/kb_*.py` and run through `make` targets from a clone of the repository. The [tagged retrieval workbook](tagged-retrieval-workbook) explains tags themselves, and the [performance workbook](performance-workbook#how-much-do-tags-improve-retrieval) gives the measured gains.

- [What does it change in the pages?](#what-does-it-change-in-the-pages)
- [What do you need?](#what-do-you-need)
- [Step 1: build a validation set](#step-1-build-a-validation-set)
- [Step 2: write the section contexts](#step-2-write-the-section-contexts)
- [Step 3: derive page tags](#step-3-derive-page-tags)
- [Step 4: measure the tags](#step-4-measure-the-tags)
- [How to use batch inference?](#how-to-use-batch-inference)
- [What does it cost?](#what-does-it-cost)

## What does it change in the pages?

A page keeps its own front matter. The scripts add two reserved keys and touch nothing else:

```yaml
---
title: Retry policy
owner: platform
retrieval:                 # page tags derived by kb-tags
  framework_or_tool: CDK
  language_or_runtime: Python
sections:                  # per-section metadata, keyed by section path
  retry-policy/backoff:
    retrieval:
      context: The backoff settings of the Cedar deployment runbook, which ...
---
```

`sections` maps a section path to metadata for the chunks of that section only. The Markdown chunker merges each entry into the chunks whose `section_path` matches, and never copies the `sections` key itself into chunk metadata. A section path is the chain of heading slugs, as the chunker reports it in each chunk's `section_path`. An entry that matches no section is logged as a warning, so a renamed heading does not fail silently. The key works for any per-section metadata, not only contexts.

Derived values go under `retrieval`, which says what they are for: they serve retrieval, in the embedding and the keyword index alike, and they never mix with tags people wrote. Pages curated before the key was renamed keep their values under `llm`; running `apply` again moves the fields it wrote there, from its cache, with no model call. The scripts never commit: review the diff before you do.

## What do you need?

- A clone of this repository with `make setup` done.
- AWS credentials allowed to call Claude Haiku 4.5 on Amazon Bedrock. The scripts use the `eu.anthropic.claude-haiku-4-5-20251001-v1:0` inference profile from `eu-central-1` by default; `--model` and `--region` change it.
- A copy of the pages to work on. The scripts rewrite front matter in place, so run them on a working copy or a branch.

To try the whole path first, without AWS and at no cost, add `--model fake` to `KB_ARGS` of every step that calls the model. A deterministic stand-in then answers each prompt in the expected shape, from the text it is given. Its answers are only meant to exercise the scripts, so do not read anything into the numbers they produce. With `--backends hashing` for `pool` and `contextual`, and `--backend hashing` for `kb-evaluate`, nothing is downloaded either. On the eight pages of `fixtures/tagged/`, every step below completes in seconds.

Each target takes `KB`, the folder of pages, and `WORK`, a folder for caches, the validation set and results (default `tmp/kb/<folder name>`). Extra options go through `KB_ARGS`. Every model answer is cached on disk by content, so a re-run costs nothing and an edited page costs only that page.

## Step 1: build a validation set

Build the questions before writing any context or tag, so that tag design cannot shape the test. The set has strata: `local` questions, answerable from one passage alone, are a control where page context should not matter; `contextual` questions are written for one of two near-identical passages from different pages, so only the page context tells them apart. Real queries, if you have a log, are the best stratum of all.

```shell
$ make kb-validation KB=pages STEP=local KB_ARGS="--size 200"
$ make kb-validation KB=pages STEP=contextual KB_ARGS="--size 150"
$ make kb-validation KB=pages STEP=import KB_ARGS="--queries real-queries.txt"
$ make kb-validation KB=pages STEP=split
```

Questions are only written from passages of at least 60 words; for a knowledge base of short sections, lower it with `KB_ARGS="--min-words 30"` on `local` and `contextual`.

`split` assigns whole pages to a dev split (about 30%) and a test split, and freezes the assignment with a digest. Choose prompts, fields and thresholds on dev only, and score test once. A later `split` refuses to change a frozen split unless you pass `--force`, which voids earlier test results.

The golden passages of each question come from pooling and judging. `pool` collects the top 20 results of several search systems; `judge` has the model grade each pooled passage 0, 1 or 2 against the question and its reference answer, without seeing any tag or context. A passage graded 1 or more is golden.

```shell
$ make kb-validation KB=pages STEP=pool
$ make kb-validation KB=pages STEP=judge KB_ARGS="--batch"
```

The judge makes one call per pooled passage, tens of thousands for a few hundred questions, so run it as a batch job (see [How to use batch inference?](#how-to-use-batch-inference)). Then check the judge against a person:

```shell
$ make kb-validation KB=pages STEP=audit KB_ARGS="--size 60"
```

`audit` writes `WORK/audit.yaml`: 60 questions, balanced across strata, each with its source passage, one the judge kept and one it rejected. Fill `question_valid` (yes or no) once per question and `human_grade` (0, 1 or 2) before each passage. The judge's grades are in `WORK/audit-judge.yaml`; do not open it before you are done. Then:

```shell
$ make kb-validation KB=pages STEP=kappa
```

`kappa` reports Cohen's κ between you and the judge, on the 0 to 2 grades and on golden or not. Golden or not is what Pass@k uses; if its κ is below 0.6, revise the judge prompt (`fixtures/prompts/relevance-judge.v*.md`) and judge again before trusting any number.

## Step 2: write the section contexts

```shell
$ make kb-context KB=pages KB_ARGS="--dry-run"
$ make kb-context KB=pages
```

For each section, the model reads the whole page and writes the short context that situates the section in it, with Anthropic's prompt word for word. The page is sent once as a cached prompt prefix and read from the cache for its other sections. `--dry-run` writes the contexts to `WORK/contexts.json` only; without it they go into each page's `sections` key. A page much longer than the model needs, such as a 300-page filing, can be bounded with `KB_ARGS="--max-document-chars 12000"`: the model then sees the opening of the page and the neighbourhood of the section instead of the whole page. For a large folder, add `--batch`.

## Step 3: derive page tags

Native tags written by people come first. Derived tags help when they name what a passage loses once cut from its page, in the words people search with: a product, a tool, a jurisdiction.

```shell
$ make kb-tags KB=pages STEP=discover
$ make kb-tags KB=pages STEP=tag
$ make kb-tags KB=pages STEP=apply KB_ARGS="--keep framework_or_tool language_or_runtime"
```

`discover` shows the model a sample of pages, stratified by folder and length, and the dev questions, and writes the proposed fields to `WORK/schema.json`. `tag` tags every page twice, the second time with the fields in reverse order, drops fields whose two passes agree on under 80% of pages, and writes `WORK/review.yaml`: 30 pages per field for you to mark `correct` yes or no. `apply` writes the fields you keep under `retrieval`. Choose which to keep on the dev split (next step), not on how plausible they look.

## Step 4: measure the tags

`kb-evaluate` indexes the same chunks once per condition, the baseline without tags and each tag set in the embedding, the keyword index, or both, and compares each with the baseline, per stratum, with a 95% paired bootstrap interval:

```shell
$ make kb-evaluate KB=pages TAGS="context=context: \$.retrieval.context" KB_ARGS="--split dev"
$ make kb-evaluate KB=pages KB_ARGS="--split dev \
    --tags 'context=context: \$.retrieval.context' \
    --tags 'derived=tool: \$.retrieval.framework_or_tool;language: \$.retrieval.language_or_runtime'"
```

A tag set is `NAME=label: $.path;label: $.path`. Results go to `WORK/results/`. Iterate on dev; when the choice is made, run `--split test` once and report that. Then go back to [step 4 of the build procedure](build-knowledge-base-procedure#step-4-prefix-chunks-with-tags-before-embedding) and index with the chosen tags, for example `--tag-field "context: $.retrieval.context" --tag-bm25`. To look at the tags that you kept, use the [inspection procedure](inspect-knowledge-base-procedure).

## How to use batch inference?

Bedrock batch inference has its own quotas, far above the daily token quota of on-demand calls, and costs half as much. It needs at least 100 requests per job and can take up to a day, though jobs of a few thousand requests usually finish within an hour. It also needs an S3 bucket in the job's region and a service role that lets `bedrock.amazonaws.com` read and write that bucket and invoke the model.

1. Run the step with `--batch`. It answers what it can from earlier answers and queues the rest in `WORK/batch/pending.jsonl`. `kb-context` then stops with exit status 3, which `make` reports as an error.
2. Submit the queue and follow the jobs:

   ```shell
   $ make kb-batch KB=pages STEP=submit BATCH_BUCKET=my-bucket BATCH_ROLE_ARN=arn:aws:iam::123456789012:role/my-batch-role
   $ make kb-batch KB=pages STEP=status
   ```

3. When the jobs are `Completed`, fetch the answers, then run the step again, with `--batch`, to finish from them:

   ```shell
   $ make kb-batch KB=pages STEP=fetch
   $ make kb-context KB=pages KB_ARGS="--batch"
   ```

`fetch` prints the tokens the jobs used. The usage report of the step itself counts on-demand calls only.

## What does it cost?

The model cost grows with the number of sections times the length of their pages, because each section's prompt carries its page. Caching the page across sections cuts most of that on demand, and batch halves the rest. The judge grows with questions times pool size. The figures measured on four corpora, from 737 to 27,922 chunks, are in the [performance workbook](performance-workbook#what-does-curation-cost). Embedding the contexts costs more than embedding tags, because a context is about 50 words where a tag is a few.

## Follow-up

- [Build a knowledge base](build-knowledge-base-procedure): the parent procedure, which indexes the tagged pages.
- [Inspect a knowledge base](inspect-knowledge-base-procedure): check the tags, their distribution and the search results.
- [Tagged retrieval workbook](tagged-retrieval-workbook): questions and answers on tags, section contexts and the evidence behind them.
- [Performance workbook](performance-workbook): the measured gains and costs.
- [Issue #65](https://github.com/bernard357/duckdb-vectors/issues/65): the method and its pre-registration.

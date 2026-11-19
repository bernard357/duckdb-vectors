# Inspect a knowledge base

This sub-procedure details [step 5 of the build procedure](build-knowledge-base-procedure#step-5-check-the-knowledge-base). A knowledge manager looks inside a DuckDB knowledge base to check its content, to review the tags and their distribution, and to try searches before agents rely on it. Run it after each indexing, and again when the pages or the tags change.

- [What do you need?](#what-do-you-need)
- [Step 1: check the content](#step-1-check-the-content)
- [Step 2: check the coverage of tags](#step-2-check-the-coverage-of-tags)
- [Step 3: review the values of each tag](#step-3-review-the-values-of-each-tag)
- [Step 4: read the tags report](#step-4-read-the-tags-report)
- [Step 5: look at the embedded prefix](#step-5-look-at-the-embedded-prefix)
- [Step 6: try searches](#step-6-try-searches)
- [Step 7: iterate](#step-7-iterate)

## What do you need?

- A knowledge base built with the [build procedure](build-knowledge-base-procedure), here `knowledge.duckdb`.
- The same `--backend` as the one used for indexing, for every verb below. A different backend embeds queries in a different space and returns noise.
- For the SQL steps, the `duckdb` Python package, which `duckdb-vectors` already installs. Open the file read-only, and not while another process is indexing it.
- A file of real questions, one per line, for steps 4 and 6. Questions taken from a log or from your users are the best. Do not write them after looking at the tags.

## Step 1: check the content

The `report` verb counts the chunks and lists the sources. Declare the tags in the environment to see what the current settings expect:

```shell
$ DUCKDB_VECTORS_TAG_FIELDS="product: $.product.name; audience: $.audience[*]" \
    duckdb-vectors report --db knowledge.duckdb
```

Check that every page you expect is among the `sources`, that the number of `chunks` is plausible for the volume of text, and that `tag_fields` shows the tags you meant. A page that is missing was not transcoded, or sits outside the folder that you indexed.

## Step 2: check the coverage of tags

A tag is useful only on the pages that carry it. Write a small script, for instance `inspect_tags.py`, that opens the file read-only and runs the SQL of this procedure:

```python
import duckdb

con = duckdb.connect("knowledge.duckdb", read_only=True)
rows = con.sql(
    """
    SELECT count(DISTINCT source) AS pages,
           count(DISTINCT source) FILTER (
               json_extract_string(metadata, '$.product.name') IS NOT NULL
           ) AS tagged
    FROM chunks
    """
).fetchall()
print(rows)
```

The `metadata` column holds the front matter of each page as JSON, with section metadata merged in, so any path that you give to `--tag-field` works here too. List the pages that lack the tag, and fill in their front matter or leave them out on purpose:

```sql
SELECT DISTINCT source
FROM chunks
WHERE json_extract_string(metadata, '$.product.name') IS NULL
ORDER BY source;
```

A tag that covers fewer than half of the pages helps little: most chunks get no context from it.

## Step 3: review the values of each tag

Count the pages for each value of a tag. A tag works when values repeat across related pages, and it fails when the same thing is written in several ways:

```sql
SELECT json_extract_string(metadata, '$.product.name') AS value,
       count(DISTINCT source) AS pages,
       count(*) AS chunks
FROM chunks
GROUP BY value
ORDER BY pages DESC;
```

Read the list from top to bottom and look for:

- __Variants__ of one value, such as `Enterprise`, `enterprise plan` and `Ent.`. Pick one spelling, and fix the front matter at the source.
- __A single value__ shared by every page. It separates nothing, so drop the tag.
- __A different value on every page__. A title can still match words of a query, while an opaque code cannot.
- __Empty answers__ such as `Other` or `None`, mostly written by language models. Remove them, since a tag that says nothing tells a search nothing.

Do the same for derived tags, under `$.retrieval`. For section contexts, which differ on every section, read a sample instead: pick ten sections, and check that each context names what its section loses when it is cut from its page.

## Step 4: read the tags report

The `tags` verb computes the figures of every declared tag over the whole index, and gives a verdict:

```shell
$ duckdb-vectors tags --db knowledge.duckdb \
    --tag-field "product: $.product.name" \
    --tag-field "kind: $.kind" \
    --queries my-queries.txt
```

| Field | What it tells you |
| --- | --- |
| `coverage` | Share of documents that carry the tag. Low coverage means most chunks get no help from it. |
| `cardinality` | Number of distinct values. |
| `entropy` | How evenly the values are spread, from 0 (one value everywhere) to 1 (every value equally common). |
| `documents_per_value` | How many documents share a value. About one adds a note: a title helps if queries use titles, an opaque code does not. |
| `query_mention` | With `--queries`: share of the queries that contain at least one value of the tag as whole words. |
| `verdict`, `reasons` | `effective` or `decorative`, and why. |
| `margin_*`, `d_*`, `value_*` | How much the tag raises the similarity of chunks of one document (`margin`, `d`) or of chunks that share its value (`value`) over other chunks, with and without the tag. Reported, not used for the verdict. |

A tag is __decorative__ when its coverage is below 50%, when it has a single value, or, with queries, when fewer than 20% of the queries contain one of its values. Every other tag is __effective__. Without `--queries` only sparse and constant tags are screened out, and each tag carries a note that says so.

> __Provisional.__ The query-mention threshold was fitted on 17 tags from validation sets that later proved unable to show what tags restore, and the later runs of [issue #65](https://github.com/bernard357/duckdb-vectors/issues/65) did not re-test it. Use it as a hint, not a verdict.

Why the verdict relies on queries: in the first benchmark, over 17 tags in four corpora, the rank correlation between the separation lift of a tag and the retrieval gain it brought was close to zero, while the share of queries that mention the values of a tag correlated at 0.62. The [performance workbook](performance-workbook#which-tags-help) says which tags helped in the later runs.

The report judges page tags. A section context such as `$.retrieval.context` is a sentence that no query contains, and it differs from section to section, so the report counts one value per page and always calls it decorative. Ignore the verdict for contexts, and judge them on your own questions, as the [tagging procedure](tag-knowledge-base-procedure#step-4-measure-the-tags) does.

The report cannot tell you whether a tag helps your searchers. It tells you which tags to try and which to drop first. Its `warnings` list also says when prefix and chunk probably exceed the input window of the model, which the next step looks at.

## Step 5: look at the embedded prefix

The `tags` column stores the prefix of each chunk, as it was embedded. Read a few, and find the longest:

```sql
SELECT chunk_id, tags, length(tags) AS prefix_characters
FROM chunks
ORDER BY prefix_characters DESC
LIMIT 5;
```

Models cut input beyond their window, at 512 tokens for every model of fastembed 0.9.1, and at 128 for the two paraphrase-multilingual models. Four characters make about one token. A prefix of several hundred characters, added to a long chunk, pushes the end of the chunk out of the window. Shorten the prefix by dropping tags that the report calls decorative. See [How long can the prefix be?](tagged-retrieval-workbook#how-long-can-the-prefix-be).

## Step 6: try searches

Run the questions of your file, one by one, and read the hits:

```shell
$ duckdb-vectors search "how do we roll back a release?" \
    --db knowledge.duckdb --top-k 5
```

Each hit carries the raw chunk, its metadata, a `score`, and its rank in the vector search (`vector_rank`) and in the keyword search (`text_rank`). Check three things:

- The right passage is among the first hits. If not, note the question, because it is a test case for the next iteration.
- A hit that comes from the keyword search only (no `vector_rank`) shows that an exact term did the work. If tags were meant to do it, check that you indexed with `--tag-bm25`.
- Two near-identical passages from different pages are told apart. This is what tags are for. If the wrong one comes first, the tag that separates them is missing or too weak.

To compare two tag sets on the same pages, index each in its own file, and run the same questions on both:

```shell
$ duckdb-vectors index ./pages --recursive --db plain.duckdb
$ duckdb-vectors index ./pages --recursive --db tagged.duckdb \
    --tag-field "product: $.product.name" --tag-bm25
$ duckdb-vectors search "refund window for the enterprise plan" --db plain.duckdb --top-k 3
$ duckdb-vectors search "refund window for the enterprise plan" --db tagged.duckdb --top-k 3
```

A handful of questions shows a problem. It does not measure a gain: for that, use the validation set of the [tagging procedure](tag-knowledge-base-procedure#step-1-build-a-validation-set).

## Step 7: iterate

1. Declare the tags you think matter, including doubtful ones.
2. Index, then run steps 1 to 5.
3. Remove the tags that the report marks as decorative, or fix the front matter behind them. A sparse tag may only need to be filled in on more pages.
4. Index again. Only the chunks whose prefix changed are embedded again.
5. Run your questions before and after, and compare the hits.

## Follow-up

- [Build a knowledge base](build-knowledge-base-procedure): the parent procedure.
- [Tag a knowledge base](tag-knowledge-base-procedure): write section contexts and page tags offline, and measure them.
- [Tagged retrieval workbook](tagged-retrieval-workbook): why tags help, which JSONPath expressions work, and how to control the vocabulary.
- [Performance workbook](performance-workbook): the measured results.

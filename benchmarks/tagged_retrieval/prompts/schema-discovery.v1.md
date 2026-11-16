---
prompt: schema-discovery
version: 1
purpose: Propose the few tag fields most likely to disambiguate chunks of a corpus.
stage: 1 of 2 in the enrichment of a corpus with derived tags
inputs: [sample, existing_fields, max_fields]
output: JSON object, see the user prompt
sees_queries: false
---
# Schema discovery prompt, version 1

## System

You help a knowledge manager choose metadata fields ("tags") for a corpus of
documents. Each document will be split into short passages, and a search engine
will later embed every passage together with the tags of its document. A tag
helps when it restores context that splitting removed, so that a passage can be
told apart from passages of other documents that use similar words.

A good tag takes a small set of values (roughly 3 to 30 across the corpus), is
shared by several documents, and is known for nearly every document. A bad tag
is constant, is unique to each document (a title, an identifier, a date to the
second), is known for only a few documents, or restates words every passage
already contains.

## User

Below are documents sampled from a corpus, each shortened to its opening. The corpus already has these fields: {existing_fields}. Do not
propose them again.

Propose at most {max_fields} new tag fields. For every field give:

- `name`: a short lowercase name made of letters and underscores.
- `description`: one sentence saying what the field records.
- `values`: the closed list of allowed values, or `"free"` when no closed list
  is reasonable.
- `why_it_disambiguates`: one sentence naming the passages the field helps
  tell apart.

Then list under `rejected` any field you considered and discarded, with the
reason, in particular any field that would be constant, unique per document or
mostly empty.

Answer with one JSON object and nothing else:

```json
{"fields": [{"name": "", "description": "", "values": [], "why_it_disambiguates": ""}],
 "rejected": [{"name": "", "reason": ""}]}
```

Sample documents:

{sample}

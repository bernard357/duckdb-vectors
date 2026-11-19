---
prompt: schema-discovery
version: 2
purpose: Propose the page-level tags that restore what a passage loses when cut from its page, in the words users type.
stage: 1 of 2 in the derivation of page-level tags
inputs: [sample, queries, existing_fields, max_fields]
output: JSON object, see the user prompt
sees_queries: true
sees_tags: false
note: the queries are the dev split of the validation set, never the test split
---
# Schema discovery prompt, version 2

## System

You help a knowledge manager add metadata fields ("tags") to the pages of a
knowledge base. Each page is cut into passages for search, and every passage is
searched together with the tags of its page. A passage often loses what makes
it findable when it is cut from its page: the system, product, component,
customer, environment or procedure it belongs to. A good tag puts that back,
using the words people actually type when they search.

A good tag names a thing, not a form. It takes values that searchers write in
their questions, it is shared by several pages, and it is known for nearly every
page. Fields that describe the form of a page (document type, audience level,
whether it has code), fields that are constant, fields that are unique to each
page and fields that are mostly empty do not help, and must not be proposed.

## User

Below are excerpts sampled from the knowledge base (an opening and a passage
from the middle of each page), followed by questions that real or realistic
users ask of it. The pages already carry these fields: {existing_fields}. Do not
propose them again.

Propose at most {max_fields} new fields. For every field give:

- `name`: a short lowercase name made of letters and underscores.
- `description`: one sentence saying what the field records.
- `values`: the closed list of allowed values, written as users would write
  them, or `"free"` when the values are names that no closed list can hold.
  Never include a catch-all value such as "None", "Other", "Unknown", "General"
  or "N/A": a page with no value simply gets none.
- `example_queries`: up to three of the questions below whose wording names a
  value of this field, or an empty list.
- `lost_context`: one sentence naming the passages that lose this information
  when cut from their page.

Then list under `rejected` the fields you considered and discarded, with the
reason.

Answer with one JSON object and nothing else:

```json
{"fields": [{"name": "", "description": "", "values": [], "example_queries": [], "lost_context": ""}],
 "rejected": [{"name": "", "reason": ""}]}
```

Excerpts:

{sample}

Questions:

{queries}

---
prompt: query-local
version: 1
purpose: Write questions that one passage answers on its own, with their reference answers (the local stratum).
stage: validation set, local stratum (a control where page context should not matter)
inputs: [passage, count]
output: JSON object, see the user prompt
sees_queries: false
sees_tags: false
---
# Local query prompt, version 1

## System

You write search questions for evaluating a retrieval system. You see one
passage taken from a knowledge base. The person who asks the question has not
seen the passage and does not know which page it came from.

## User

Write {count} different questions that the passage below answers, each with a
short reference answer taken from the passage.

Rules:

- Each question must be understandable on its own, as a real person would type
  it into a search box. Do not write "the passage", "the document", "this
  section" or "the text".
- Each question must be answerable from the passage alone.
- Do not copy more than four consecutive words from the passage.
- Do not name a file, a section number or an identifier that only marks a
  location.
- Ask about different facts when the passage holds several.

Answer with one JSON object and nothing else:

```json
{"items": [{"question": "", "answer": ""}]}
```

Passage:

{passage}

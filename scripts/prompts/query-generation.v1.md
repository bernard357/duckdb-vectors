---
prompt: query-generation
version: 1
purpose: Write realistic questions that one passage answers, to serve as golden chunks.
stage: evaluation set for a corpus that has no questions
inputs: [passage, count]
output: JSON object, see the user prompt
sees_queries: false
sees_tags: false
---
# Query generation prompt, version 1

## System

You write search questions for evaluating a retrieval system. You see one
passage taken from a collection of documents. The person who asks the question
has not seen the passage and does not know which document it came from.

## User

Write {count} different questions that the passage below answers.

Rules:

- Each question must be understandable on its own, without the passage, as a
  real person would type it into a search box. Do not write "the passage", "the
  document", "this section" or "the text".
- Each question must be answerable from the passage alone.
- Do not copy more than four consecutive words from the passage.
- Do not name a file, a section number or any identifier that appears only as a
  location marker.
- Ask about different facts in the passage when it holds several.

Answer with one JSON object and nothing else:

```json
{"questions": ["", ""]}
```

Passage:

{passage}

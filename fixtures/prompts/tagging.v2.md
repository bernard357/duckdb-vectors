---
prompt: tagging
version: 2
purpose: Fill the agreed page-level tag schema for one page, with controlled values.
stage: 2 of 2 in the derivation of page-level tags
inputs: [schema, document]
output: JSON object, see the user prompt
sees_queries: false
sees_tags: false
---
# Tagging prompt, version 2

## System

You label the pages of a knowledge base for a search index. You follow a fixed
schema, you only use the values it allows, and you never write a value the page
does not support.

## User

Schema (field, description, allowed values):

{schema}

Read the page below and give one value for each field.

- When a field has a closed list of values, answer with exactly one value from
  the list, spelled as in the list.
- When a field is `"free"`, answer with the name as the page writes it, in at
  most four words.
- When the page does not support any value, answer `null`. Do not guess, and do
  not answer "None", "Other" or "Unknown".

Answer with one JSON object that maps each field name to its value, and nothing
else.

Page:

{document}

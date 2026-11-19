---
name: transcode-to-markdown
description: >
  Convert Word, PowerPoint and OpenDocument files (.docx, .odt, .pptx, .odp)
  into Markdown, so that a folder of reference documents can be indexed and
  searched with duckdb-vectors. Runs Microsoft markitdown on demand with uvx
  (no install), after converting OpenDocument files with LibreOffice.
  Use this whenever the user wants to turn office documents into Markdown,
  prepare a corpus of Word or PowerPoint files for a knowledge base, or index
  documents that duckdb-vectors cannot read directly.
  Triggers on: "convert to markdown", "transcode documents", "docx to
  markdown", "pptx to markdown", "odt to markdown", "odp to markdown",
  "prepare documents for the knowledge base", "markitdown".
  Do NOT use for PDF files (duckdb-vectors indexes them directly), for
  spreadsheets, for scanned documents that need OCR, or for searching an
  existing index (use search-duckdb-vectors).
---
# Transcode office documents to Markdown
Turn `.docx`, `.odt`, `.pptx` and `.odp` files into Markdown with
[markitdown](https://github.com/microsoft/markitdown), run with `uvx`, so that
`duckdb-vectors index` can split them on headings and search them. This is a
best-effort basic tool: read [Known limits](#known-limits) before promising
anything to the user.
## Prerequisites
- [`uv`](https://docs.astral.sh/uv/) for `uvx`.
- LibreOffice, only for `.odt` and `.odp` files. Find the `soffice` binary
  first on the `PATH`, then at `/Applications/LibreOffice.app/Contents/MacOS/soffice`
  on macOS. If neither exists, stop and tell the user to install LibreOffice
  (`brew install --cask libreoffice` on macOS), or to export the files to
  `.docx` and `.pptx` themselves.
## Convert one file
Pick the command by extension. `<out>` is the Markdown file to write.
```shell
$ uvx --from 'markitdown[docx,pptx]' markitdown <file.docx|file.pptx> -o <out>.md
```
For `.odt` and `.odp`, convert to the matching OOXML format in a scratch
folder first, then run the command above on the result:
```shell
$ soffice --headless --convert-to docx --outdir <scratch> <file.odt>
$ soffice --headless --convert-to pptx --outdir <scratch> <file.odp>
```
## Convert a folder
Mirror the source tree under an output folder, one `.md` per source file.
Two files with the same name and different extensions would collide, so name
the output after the full file name (`report.docx.md`) when that happens.
```shell
$ mkdir -p <outdir>
$ for f in <srcdir>/*.docx <srcdir>/*.pptx; do
>   uvx --from 'markitdown[docx,pptx]' markitdown "$f" -o "<outdir>/$(basename "$f").md"
> done
```
Convert the OpenDocument files the same way after the `soffice` step.
## Check the result
Open each output and look for the losses listed below. Report to the user
which files lost what. Do not try to repair them in this version.
## Known limits
Measured on one sample file per format with every element of interest.
| Element | .docx / .odt | .pptx / .odp |
| --- | --- | --- |
| Text blocks | kept | kept, with speaker notes and a `<!-- Slide number: N -->` marker |
| Headings | kept, from styles | slide title becomes `#`, no deeper levels |
| Links | kept | lost |
| Bold and italic | kept | lost |
| Highlighted text | lost, rendered as plain text | lost |
| Tables | kept as pipe tables, merged cells are flattened, header row may be empty | kept as pipe tables, merged cells are flattened |
| Pictures | lost, replaced by a truncated `data:` placeholder | lost, the image links point to files that are not written |
| Diagrams | not converted, they are pictures | not converted, they are pictures |
Other limits:
- PDF files are not converted. markitdown returns flat text without headings,
  links or emphasis, which is no better for chunking than the PDF chunker of
  duckdb-vectors. Index PDFs directly.
- The `.odt` and `.odp` results equal the `.docx` and `.pptx` results, because
  they go through LibreOffice first.
## Privacy
Everything runs on the local machine. Call markitdown on local files only. Do
not enable its cloud options (Azure Document Intelligence, Azure Content
Understanding, LLM image captioning), because they send document content to a
remote service.
## Next step
Index the output folder and search it:
```shell
$ uvx duckdb-vectors index <outdir>
$ uvx duckdb-vectors search "<question>"
```
See the `search-duckdb-vectors` skill for searching.

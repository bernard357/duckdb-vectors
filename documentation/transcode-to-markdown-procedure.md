# Turn Word and PowerPoint files into a searchable knowledge base

Here we convert a folder of office documents (`.docx`, `.odt`, `.pptx`, `.odp`) into Markdown, then index the result with `duckdb-vectors`. Conversion is done by [markitdown](https://github.com/microsoft/markitdown), run on demand with `uvx`, so nothing is installed. The same steps are packaged as the agent skill `skills/transcode-to-markdown`, which an agent can follow on its own.

This is a basic, best-effort path. Headings, text, tables and, for Word files, links and bold are kept. Highlights and pictures are lost. See [Known limits](#known-limits).

PDF files are not part of this procedure. `duckdb-vectors` indexes them directly, and markitdown returns flat text without headings for them.

## Step 1: check the prerequisites

You need [`uv`](https://docs.astral.sh/uv/). For `.odt` and `.odp` files you also need LibreOffice, whose `soffice` command converts them to Word and PowerPoint formats first. On macOS, install it with `brew install --cask libreoffice`. The binary sits at `/Applications/LibreOffice.app/Contents/MacOS/soffice` and is usually not on your `PATH`.

## Step 2: convert OpenDocument files

Skip this step if you only have `.docx` and `.pptx` files.

```shell
$ mkdir -p scratch
$ soffice --headless --convert-to docx --outdir scratch my-report.odt
$ soffice --headless --convert-to pptx --outdir scratch my-slides.odp
```

## Step 3: convert to Markdown

Run markitdown on each file. The output is named after the full file name, so that `report.docx` and `report.odt` do not overwrite each other.

```shell
$ mkdir -p knowledge
$ for f in documents/*.docx documents/*.pptx scratch/*.docx scratch/*.pptx; do
>   uvx --from 'markitdown[docx,pptx]' markitdown "$f" -o "knowledge/$(basename "$f").md"
> done
```

## Step 4: index and search

```shell
$ uvx duckdb-vectors index knowledge
$ uvx duckdb-vectors search "your question"
```

Each result carries the heading of its section, because Word headings and slide titles become Markdown headings, and chunks are split on them. A table stays in one chunk.

## Known limits

| Element | .docx and .odt | .pptx and .odp |
| --- | --- | --- |
| Text blocks | kept | kept, with speaker notes |
| Headings | kept | slide title only |
| Links | kept | lost |
| Bold and italic | kept | lost |
| Highlighted text | lost | lost |
| Tables | kept, merged cells flattened | kept, merged cells flattened |
| Pictures and diagrams | lost | lost |

Everything runs on your machine. Do not enable the cloud options of markitdown, which send content to a remote service.

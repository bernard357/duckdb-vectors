"""Generate the synthetic scanned PDF fixture used to exercise the OCR path.

Run once (committed output lives at ``fixtures/pdf/synthetic-scanned.pdf``). The
fixture is an image-only PDF: known text is rendered to a bitmap and wrapped in
a PDF with no text layer, so :class:`PdfChunker` finds nothing to extract and
falls back to local OCR. Unlike the born-digital fixtures, OCR output is not
byte-stable across engine versions, so the companion test asserts on recovered
tokens rather than an exact-match baseline.

The rendered lines deliberately include an acronym (``ESRS``) and distinct
marker words so the OCR regression test can assert on robust, unambiguous
tokens.

Usage (from the repo root, with the PDF deps installed):

    $ .venv/bin/python scripts/make_ocr_fixture.py
"""

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

_FIXTURES = Path(__file__).resolve().parent.parent / "fixtures" / "pdf"
OUT = _FIXTURES / "synthetic-scanned.pdf"

# Known content the OCR test asserts on. Keep words unambiguous for OCR.
LINES = (
    "Scanned document fixture for duckdb-vectors.",
    "This page is an image with no embedded text layer.",
    "OCR must recover these marker words:",
    "ESRS alpha bravo charlie delta echo.",
)

_FONT_CANDIDATES = (
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/Library/Fonts/Arial.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
)


def _font(size: int) -> ImageFont.ImageFont:
    for path in _FONT_CANDIDATES:
        if Path(path).exists():
            return ImageFont.truetype(path, size)
    return ImageFont.load_default()


def _companion_markdown() -> str:
    """The ground-truth text rendered into the image, as a one-page baseline.

    Unlike the born-digital companions (which freeze the chunker's exact
    output), this records what the image *says* — the source of truth the OCR
    test checks tokens against, since OCR output itself is not byte-stable.
    """
    body = " ".join(LINES)
    return f"# {OUT.name}\n\n## Page 1\n\n{body}\n"


def main() -> int:
    image = Image.new("RGB", (1240, 520), "white")
    draw = ImageDraw.Draw(image)
    font = _font(36)
    y = 48
    for line in LINES:
        draw.text((48, y), line, fill="black", font=font)
        y += 110
    OUT.parent.mkdir(parents=True, exist_ok=True)
    image.save(OUT, "PDF", resolution=150.0)
    companion = OUT.with_suffix(".md")
    companion.write_text(_companion_markdown(), encoding="utf-8")
    root = OUT.parent.parent.parent
    print(f"wrote {OUT.relative_to(root)} ({OUT.stat().st_size} bytes)")
    print(f"wrote {companion.relative_to(root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

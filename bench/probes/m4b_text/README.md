# The text probe of M4b

A prototype that reads a page's glyphs from its content stream with QPDF's
tokens (pikepdf), for the decision rule of the probe in
[ROADMAP.md](../../../ROADMAP.md), M4b. It is not part of pdfacts and is not
merged; what the probe decides is recorded in the ROADMAP and the CHANGELOG.

| File | What it does |
| --- | --- |
| `interpreter.py` | the prototype: text state, simple, composite (Identity-H) and Type3 fonts, ToUnicode, form XObjects; pdfacts glyphs from its output |
| `readable.py` | criterion 3: every page PDFium reads, read by the prototype |
| `timing.py` | criterion 2: processor time per corpus file, against PyMuPDF's text extraction |

Criterion 1, the first consumer's comparison, runs outside this repository.

Run from this directory with the bench group:

```bash
uv run --group bench python readable.py <corpus directory>
uv run --group bench python timing.py <corpus directory>
```

The metrics of the standard 14 fonts and the Adobe glyph list are pypdf's
tables (BSD; the AFM files' notice is in `pypdf._codecs.core_font_metrics`).

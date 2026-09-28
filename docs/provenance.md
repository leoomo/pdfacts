# Provenance

Where the knowledge in this library comes from, and what may not be copied
into it.

## Rule

Reading the source code of MuPDF and PyMuPDF to learn from it is allowed.

**Their code is not copied or translated line by line into pdfacts.** Both are
licensed under the AGPL. A work that contains their code has to be released
under the AGPL as well, which would defeat the purpose of this library.

In practice:

- Understand the idea, close the source, write the implementation with its own
  structure and names.
- When a module was written after reading third-party source, add a row to the
  log below.
- Behaviour rules are covered by constructed test files. This is first of all
  for correctness; it also shows that the implementation follows behaviour.
- MuPDF bindings appear only under `bench/`. The library, its tests and its
  tools never import them. `tools/check_architecture.py` checks the import
  statements for this; an import made at run time, for example with
  `importlib`, is not detected.
- Coding agents may read third-party source. They must not paste or translate
  AGPL code.

Before third-party code is reused, its license is checked and the component is
added to `THIRD_PARTY.md`.

This document is an engineering rule, not legal advice.

## Log

| Date | Module | Source consulted | What was taken |
| --- | --- | --- | --- |
| 2026-09-27 | `backends/pdfium.py`, `backends/qpdf.py` | pypdfium2 and pikepdf documentation | version attributes |
| 2026-09-28 | `backends/pdfium.py` (font policy) | PDFium source, branch `chromium/7999`: `fpdfsdk/fpdf_sysfontinfo.cpp`, `public/fpdf_sysfontinfo.h` (BSD-3-Clause) | that every callback of an external font info is checked for null, and that only versions 1 and 2 are accepted |
| 2026-09-28 | `backends/qpdf.py` | pikepdf source: `_methods.py`, `_explicit_conv.py` (MPL-2.0) | that the input stream is named by its `repr` and kept referenced; the order in which conversion modes apply |
| 2026-09-28 | `backends/pdfium.py` (open) | pypdfium2 source: `_helpers/document.py`, `_open_pdf` and `PdfDocument` (Apache-2.0 or BSD-3-Clause); PDFium `public/fpdfview.h` | the load call and how the password is passed; that a document without pages is refused with its handle left open; the error codes |
| 2026-09-28 | `backends/pdfium.py` (rendering) | pypdfium2 source: `_helpers/page.py`, `PdfPage.render` and `_parse_renderopts` (Apache-2.0 or BSD-3-Clause) | how the size of the bitmap follows from the page size, the scale and the crop; that a crop leaving less than one pixel raises ValueError before a bitmap is made; which flags grey and the reversed byte order set |

## Knowledge gathered before the first implementation

Recorded so that later modules can cite it.

| Topic | How it was obtained |
| --- | --- |
| How the reference engine turns path operators into lines, rectangles and quads | black-box tests on constructed files |
| Page geometry under rotation, crop box and UserUnit | constructed files compared with rendering |
| Thresholds for joining glyphs into words and lines | recalled from MuPDF's structured text code; to be pinned by constructed cases before use |
| PDFium text interface behaviour: omitted spaces, line-end hyphen marker, ligature expansion, angle sign | measurement on the corpus |

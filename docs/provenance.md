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
| 2026-09-28 | `backends/qpdf.py` (recovery) | pikepdf source: `_methods.py`, `Pdf.save` (MPL-2.0) | the options of `save`: `encryption`, `object_stream_mode`, `linearize`, `deterministic_id`; that `save` writes through `_save` and adds a warning about widgets the form does not reach; `_cpphelpers.py`, `update_xmp_pdfversion`: that `fix_metadata_version` writes the XMP anew |
| 2026-09-28 | `backends/pdfium.py` (rendering) | pypdfium2 source: `_helpers/page.py`, `PdfPage.render` and `_parse_renderopts`; `_helpers/bitmap.py`, `PdfBitmap.new_native`, `new_foreign` and `_get_buffer`; `internal/bases.py`, `AutoCloseable` (Apache-2.0 or BSD-3-Clause) | how the size of the bitmap follows from the page size, the scale and the crop; that a crop leaving less than one pixel raises ValueError before a bitmap is made; which flags grey and the reversed byte order set; that the default bitmap is made with `needs_free=False` and never destroyed in PDFium, that `new_foreign` is destroyed on close, and what `force_packed` does |
| 2026-10-01 | `text/layout.py` | MuPDF's structured text device (`stext-device.c`, AGPL), as recalled in the measurements of 2026-09-27; nothing copied | measuring the pen's movement along and across the baseline in units of the font size; the thresholds 0.15 for a space and 0.8 for a new line |
| 2026-10-03 | `backends/qpdf.py` (active content) | MuPDF source, branch `master`: `source/pdf/pdf-form.c`, `pdf_execute_action_chain`, the document, page, annotation and field events, and `pdf_calculate_form`; `source/pdf/pdf-js.c`, `pdf_js_load_document_level`; `source/pdf/pdf-stream.c`, `pdf_obj_num_is_stream`; `source/pdf/pdf-object.c`, `pdf_dict_get`; `source/pdf/pdf-nametree.c`, `pdf_load_name_tree` (AGPL); read, nothing copied | the entries that name an action to run and their events, and the JavaScript name tree; that `/Next` names a dictionary or an array of them; that a stream is read as its dictionary wherever a dictionary is read; that the fields of `/AcroForm`'s `/CO` have their C run. Not taken: MuPDF runs an action again where `/Next` names it again in another branch, which a listing cannot follow, so each action is listed once and named by its index; it reads `/AA` through `/Parent`, which a listing of what is written does not |
| 2026-10-07 | `backends/adobe_fonts.py`, `tools/adobe_fonts.py` | Adobe's Core 14 AFM files and their ReadMe; the Adobe Glyph List (`agl-aglfn`, BSD-3-Clause) | the data the module holds, extracted with the notices, as `THIRD_PARTY.md` lists; WinAnsiEncoding and MacRomanEncoding from Python's `cp1252` and `mac_roman` codecs and ISO 32000-1, Annex D |
| 2026-10-07 | `backends/fonts.py`, `backends/content_text.py` | ISO 32000-1, 8.4, 9.3, 9.4, 9.6 to 9.10; Adobe Type 1 Font Format, 2.3; the text probe of M4b (`bench/probes/m4b_text` on `probe/m4b-text-interpreter`), written for pdfacts | the text state and the fonts; nothing of a third party's code |
| 2026-10-07 | `text/glyphs.py` (`TextRules.V2` boxes) | the output of PyMuPDF 1.27.2.3 on the corpus, measured (2026-10-06); its source not read | that a glyph box whose ascent and descent span less than the size is scaled to span it |
| 2026-10-07 | `backends/fonts.py` (the heights of a font descriptor) | MuPDF source, branch `master`: `source/pdf/pdf-font.c`, `pdf_load_font_descriptor`; `source/fitz/font.c`; `include/mupdf/fitz/font.h` (AGPL); read, nothing copied. PDFium's glyph boxes on the corpus, measured | that a positive `/Descent` is read as its negation, which PDFium does too. Not taken: MuPDF's heights of 0.8 and −0.2 for an `/Ascent` at or below 0, which pdfacts would make up; it takes the top of the descriptor's `/FontBBox` instead |

## Knowledge gathered before the first implementation

Recorded so that later modules can cite it.

| Topic | How it was obtained |
| --- | --- |
| How the reference engine turns path operators into lines, rectangles and quads | black-box tests on constructed files |
| Page geometry under rotation, crop box and UserUnit | constructed files compared with rendering |
| Thresholds for joining glyphs into words and lines | recalled from MuPDF's structured text code; to be pinned by constructed cases before use |
| PDFium text interface behaviour: omitted spaces, line-end hyphen marker, ligature expansion, angle sign | measurement on the corpus |

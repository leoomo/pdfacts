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
  tools never import them. `tools/check_architecture.py` enforces this.
- Coding agents may read third-party source. They must not paste or translate
  AGPL code.

Before third-party code is reused, its license is checked and the component is
added to `THIRD_PARTY.md`.

This document is an engineering rule, not legal advice.

## Log

| Date | Module | Source consulted | What was taken |
| --- | --- | --- | --- |
| 2026-09-27 | `backends/pdfium.py`, `backends/qpdf.py` | pypdfium2 and pikepdf documentation | version attributes |

## Knowledge gathered before the first implementation

Recorded so that later modules can cite it.

| Topic | How it was obtained |
| --- | --- |
| How the reference engine turns path operators into lines, rectangles and quads | black-box tests on constructed files |
| Page geometry under rotation, crop box and UserUnit | constructed files compared with rendering |
| Thresholds for joining glyphs into words and lines | recalled from MuPDF's structured text code; to be pinned by constructed cases before use |
| PDFium text interface behaviour: omitted spaces, line-end hyphen marker, ligature expansion, angle sign | measurement on the corpus |

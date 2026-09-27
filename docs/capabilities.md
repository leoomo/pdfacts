# Capabilities

What the library can do today, and what is known about the engines it builds
on. Three states are used throughout:

| State | Meaning |
| --- | --- |
| proven | implemented and covered by a test or a recorded run |
| planned | designed, not implemented |
| unknown | not examined |

## Library

| Capability | State | Evidence |
| --- | --- | --- |
| Engine identity (`engine_info`) | proven | `tests/test_engine_info.py` |
| Import boundaries | proven | `tests/test_architecture.py`; the tests fail when the checker is disabled |
| Everything else | planned | [ROADMAP.md](../ROADMAP.md) |

## Milestone M0: install gate

Run on 2026-09-27 with the wheel `pdfacts-0.0.1-py3-none-any.whl`, installed
into an empty environment, followed by `import pdfacts; pdfacts.engine_info()`.

| Platform | Python | Result |
| --- | --- | --- |
| macOS arm64 | 3.13.5 | installed, engines reported |
| Linux x86_64, container `python:3.13-slim`, emulated on an arm64 host | 3.13.15 | installed, engines reported |
| Linux arm64, container `python:3.13-slim` | 3.13.15 | installed, engines reported |
| Linux x86_64, container `python:3.11-slim`, emulated on an arm64 host | 3.11.16 | installed, engines reported |

All four reported pypdfium2 5.13.0, PDFium 153.0.7999.0, pikepdf 10.14.0 and
QPDF 12.4.1.

Not run: Windows, musl-based Linux, native x86_64 hardware.

The minimum Python version was raised to 3.13 after this run. The 3.11 row is
kept as a record of what was run.

## What the engines provide

Measured before the first implementation, on 109 files (104 official German
forms plus five test files; 474 pages, 7935 widgets, about one million
glyphs). These are findings about the engines, not features of pdfacts.

### PDFium through pypdfium2

| Topic | Finding |
| --- | --- |
| Glyphs | character, origin, tight and loose box, font size, font ascent and descent |
| Words and lines | not provided; they have to be built from glyphs |
| Space glyphs | about 12% are omitted |
| Hyphen at the end of a line | returned as U+0002 |
| Ligatures | expanded into several characters at one origin |
| Character angle | sign is opposite to the writing direction |
| Paths | segments, draw mode, colours, stroke width; nested form XObjects are reachable |
| Path operators | `re`, `m l l l h` and `m l l l l h` cannot be told apart |
| Widget appearance streams | objects are reachable through the annotation interface |
| Widgets | name, alternate name, type, flags including inherited ones, value, options, export value, checked state |
| Object numbers | not exposed |
| Page boxes | `FPDF_GetPageBoundingBox` resolves inheritance; `FPDFPage_GetMediaBox` does not |
| UserUnit | no accessor |
| Fonts that are not embedded | taken from the host unless a font handler is installed that ignores it |
| Truncated files | fail to load |
| Saving | keeps encryption; not usable for repair |
| Threads | calls must not run concurrently |

### QPDF through pikepdf

| Topic | Finding |
| --- | --- |
| Object access | dictionaries, arrays and streams with object numbers |
| Object count | trailer `/Size` |
| Rewrite | produces a file that other libraries can read and write |
| Decryption | removes owner-password encryption on save |
| Truncated files | recovered |
| Page attributes | inherited attributes are pushed onto the pages |
| Incremental save | not available; every save rewrites the file |
| Minimum Python version | 3.11 for pikepdf 10.14.0 |

### Across platforms

| Topic | Finding |
| --- | --- |
| Paths and widgets | identical on macOS arm64, Linux arm64 and Linux x86_64 for all 104 forms |
| Glyphs with host fonts in use | different for the 28 forms that contain fonts which are not embedded |
| Glyphs with host fonts ignored | identical for all 104 forms, compared at 0.001 pt |
| Unrounded values, Linux arm64 against Linux x86_64 | glyph, path and widget digests identical for all 104 forms |
| Unrounded values, macOS against Linux | paths and widgets identical for all 104 forms; glyphs not measured with host fonts ignored |

## Corpus

`bench/corpus/manifest.csv` lists 104 forms with size and SHA-256. 91 have a
source address. The files themselves are not part of the repository.

| Check | Result on 2026-09-27 |
| --- | --- |
| all 104 local files match the manifest | yes |
| download of four entries (09, 20, 60, 95) | all four matched the manifest |
| download of the remaining 87 entries with a source | not run |

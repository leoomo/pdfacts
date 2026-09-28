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
| Opening a document, classification of the input, limits (`open`, `Document`, `Limits`) | proven | `tests/test_open.py`, `tests/test_limits.py`; all 104 corpus files open |
| Page geometry (`PAGE_GEOMETRY`) | proven | `tests/test_pages.py`, including where PDFium draws a rectangle of known position |
| Object numbers of pages (`OBJECT_NUMBERS`), `UserUnit` (`USER_UNIT`) | proven | `tests/test_pages.py`; on the corpus both engines agree on all 467 pages |
| Rendering a page or a region (`RENDERING`) | proven | `tests/test_render.py`; the first pages of five corpus files gave the same bytes on macOS arm64, Linux arm64 and Linux x86_64 |
| Recovering a damaged file (`RECOVERY`) | proven | `tests/test_recovery.py`. Five corpus forms cut at 70 % and 95 %: three of the ten recovered, with the geometry and the form fields of the original, one of them without its page's content, which lay after the cut; seven refused, QPDF finding no `/Kids` in the root of the page tree. All five keep their objects in object streams. Every corpus file with `%%EOF` removed, its last byte cut, or junk, Ctrl-Z or a vertical tab appended recovered with the geometry of the original; cut right after the offset of its last `startxref`, the 90 unencrypted files recovered and the 14 encrypted ones were refused. Of the 32 files with an incremental update, cut 200 bytes into it: 25 recovered with the geometry of the original, two with 2 of 3 pages, three refused; two updates were shorter |
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
| Rewriting a rebuilt file (2026-09-28) | when QPDF could not read the damaged file's trailer, it writes a trailer without `/Size`: the result opens only with recovery on, with warnings. With `/Size` set before saving, the result opens with recovery off and without warnings |
| Object streams in a rebuilt file (2026-09-28) | generated without `/Size` set, the result does not open even with recovery on; with `/Size` set, it opens as the others do |
| Encryption on save (2026-09-28) | `save()` drops encryption, a user password's too, unless `encryption=True`, which keeps it; an encrypted result differs from run to run |
| `/ID` on save (2026-09-28) | made from the current second, so the same document saved in two different seconds gives other bytes; `deterministic_id=True` makes it from the content, and cannot be used with encryption |
| An object at the end of a cut file (2026-09-28) | dropped when only white space follows its `endobj` to the end ("EOF after endobj"); a comment after it keeps it |
| Where QPDF looks for `startxref` (2026-09-28) | in the last 1054 bytes: one that begins 1054 bytes before the end is found, one 1055 bytes before is not, and QPDF rebuilds the table, warning that `startxref` was more than 1024 bytes before the end. It passes over a `startxref` without an offset, one glued to its offset and one whose offset other characters follow, for the one before, without a warning; a comment or a sign before the offset is taken |
| A file cut after an earlier `%%EOF` (2026-09-28) | read through the earlier revision's `startxref` when it lies within those 1054 bytes, without a warning; with a longer comment appended, the file is rebuilt from its objects, the later object of each number counting, a broken later copy too |
| Rebuilding from the objects (2026-09-28) | QPDF reads through a `startxref` that follows the last object, warning that `startxref` was more than 1024 bytes before the end of the file, where the table its offset names leads to a page tree: an offset cut short can name an earlier table. Fails for an encrypted file whose objects are in object streams: "root of pages tree has no /Kids array" |
| A file whose trailer is lost (2026-09-28) | rebuilt as unencrypted, the encryption dictionary no longer named: encrypted strings and streams stay as they are |
| Warnings while saving (2026-09-28) | a stream object is parsed when it is first used, at the latest when it is written: a wrong `/Length` can be reported by `save()` |
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

`bench/corpus/manifest.csv` lists 104 forms with size and SHA-256. The files
themselves are not part of the repository.

| Check | Result on 2026-09-27 |
| --- | --- |
| all 104 local files match the manifest | yes |
| every source downloaded and compared with the manifest | 98 served exactly the listed bytes |
| sources that serve other bytes now | 5: entries 35, 61, 70, 71, 77b |
| entries without any published source | 1: entry 01; the local file is a re-saved copy of the published form and differs from it in size and hash |

Notes on the sources

| Observation | Entries |
| --- | --- |
| the address needs its query string; without it the host answers with a web page | 07, 35 to 43, 70, 78, 97 |
| the host that serves the listed bytes is not the publisher of the form | 03b, 11 |
| the edition printed on the form is several years old | 03b (November 2019), 11 (January 2018) |
| one host answered with status 429 when asked every 1.5 seconds and served every file when asked every 15 seconds | entries on `berlin.de` |

A corpus that depends on addresses stays reproducible only as long as the
publishers keep the files. Five entries were lost that way between collection
and this check. The durable copy of the corpus is kept privately by the
maintainer.

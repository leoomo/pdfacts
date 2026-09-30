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
| Error types, values that are unknown, the capability table (`PdfactsError`, `Unknown`, `capabilities()`, `require()`) | proven | `tests/test_errors.py`, `tests/test_values.py`, `tests/test_capabilities.py` |
| Fonts installed on the host are ignored (`FONT_POLICY_EMBEDDED_ONLY`) | proven | `tests/test_runtime.py`; the M1 gate: the glyph data of all 104 corpus files identical on macOS arm64, Linux arm64 and Linux x86_64 |
| Opening a document, classification of the input, limits (`open`, `Document`, `Limits`) | proven | `tests/test_open.py`, `tests/test_limits.py`; all 104 corpus files open |
| Page geometry (`PAGE_GEOMETRY`) | proven | `tests/test_pages.py`, including where PDFium draws a rectangle of known position |
| Object numbers of pages (`OBJECT_NUMBERS`), `UserUnit` (`USER_UNIT`) | proven | `tests/test_pages.py`; on the corpus both engines agree on all 467 pages |
| Rendering a page or a region (`RENDERING`) | proven | `tests/test_render.py`; the first pages of five corpus files gave the same bytes on macOS arm64, Linux arm64 and Linux x86_64 |
| Form fields and widgets (`FORMS`) | proven | `tests/test_forms.py`; on the corpus, 7934 widgets in 75 files with the run-time check passing on all of them, and 6960 terminal fields in 75 files; the M2 gate |
| Recovering a damaged file (`RECOVERY`) | proven | `tests/test_recovery.py`. Every corpus file with its end damaged six ways (`%%EOF` removed, its last byte cut, cut right after the offset of its last `startxref`, junk, Ctrl-Z or a vertical tab appended) recovered with the geometry of the original. The 32 files with an incremental update, cut 200 bytes into it: 30 refused as ending inside an update; two updates were shorter. Five corpus forms cut at 70 % and 95 %: one of the ten recovered, with the geometry and the form fields of the original; two refused as ending inside an update; seven refused, QPDF finding no `/Kids` in the root of the page tree. All five keep objects in object streams |
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

## Milestone M1: gate

Run on 2026-09-29 with `bench/gates/run.py` on commit `46f27c6db8fe880fb830ca972b8c0f4a7d1f34b3`, over the 104
files of `bench/corpus/manifest.csv`: glyph data read through PDFium after the
font policy was installed, and page geometry read through `pdfacts.open`.
Every platform reported pypdfium2 5.13.0, PDFium 153.0.7999.0, pikepdf 10.14.0,
QPDF 12.4.1 and the font policy embedded-only.

| Platform | `uname -m` | Python | Files read | Pages | Glyphs | Seconds |
| --- | --- | --- | --- | --- | --- | --- |
| macOS arm64, natively | arm64 | 3.13.5 | 104 | 467 | 1104117 | 13.1 |
| Linux arm64, container `python:3.13.5-slim` | aarch64 | 3.13.5 | 104 | 467 | 1104117 | 19.0 |
| Linux x86_64, container `python:3.13.5-slim`, emulated on an arm64 host | x86_64 | 3.13.5 | 104 | 467 | 1104117 | 25.0 |

| Compared on all three platforms | Files identical |
| --- | --- |
| page geometry | 104 of 104 |
| glyph data, unrounded | 104 of 104 |

No file failed, and no page failed the check that both engines describe it
alike. Glyphs are those the engine did not generate itself. Seconds are the
time the gate script took to read the corpus.

The other parts of the gate are shown by tests on constructed files and by the
architecture check. They ran with `python3 tools/checks.py --all` on commit
`4c91929eda644f66d28107546a182e442b325acd`, the merge of #41 and the last
change to the code of M1. Every check passed: 1218 tests on macOS arm64, and
1217 on Linux arm64 and on Linux x86_64, where one test that needs macOS is
skipped.

| Part of the gate in `ROADMAP.md` | Shown by |
| --- | --- |
| constructed geometry cases | `tests/test_pages.py`, `test_the_geometry_of_every_geometry_case` over the 23 cases of `cases.GEOMETRY`: a plain page, and the same page with its rectangle red; rotation 0, 90, 180 and 270, and -90, 450 and 45 as written; a crop box inside the media box, also rotated, and one larger than it; a media box with a shifted origin and one with reversed corners; the media box, alone and with the rotation, inherited from the root of the page tree; no media box; UserUnit 2, 2.5 and three invalid values; three pages. The value of UserUnit: `test_a_user_unit_that_is_absent_or_a_number_above_zero` and `test_a_user_unit_that_is_not_a_number_above_zero_is_invalid`, and `test_user_unit_does_not_scale_display_space` in `tests/test_geometry.py`. `test_the_ink_is_where_the_geometry_says` checks where PDFium draws; `tests/test_geometry.py` the transforms. An inherited crop box, and values inherited from further up than the parent, are tested on the QPDF side only, in `tests/test_backend_qpdf.py` |
| glyph data identical on three platforms for every corpus file | the run above: 104 of 104 files |
| unrounded digests compared on three platforms, the result deciding the digest | the run above: identical, so exact values are hashed (`docs/conventions.md`, "Digest") |
| invalid input maps to a specific error | `tests/test_open.py`: empty input `EmptyInputError`; a header only `DamagedFileError`; garbage `NotPdfError`, and after a header `DamagedFileError`; a user password not given or wrong `PasswordRequiredError` |
| a truncated file is reported as damaged by `open` and is readable after explicit recovery; a failed recovery is a failure | `tests/test_open.py`: six truncated files, each `DamagedFileError`. `tests/test_recovery.py`: four of them recovered with the pages they kept, each result opening with every page passing the cross-check; a fifth, a rotated page with a crop box, recovered with its geometry (`test_the_geometry_survives_recovery`); the sixth, cut inside the catalog, and a header alone fail with `RecoveryFailedError`; a rewritten file without pages is a `RecoveryFailedError`, not an empty result (`test_the_verification_catches_a_result_without_pages`) |
| the architecture check passes | the step "architecture check" of that run |

A gap found after the gate was set: a file whose last whole revision is
followed by part of a revision opens with the pages of that last whole
revision, without a warning. #32 closes the gap in M2.

## Milestone M2: gate

Run on 2026-09-30 with `bench/gates/m2.py` on commit `2cb16f131acb9a87e1bf536292bbe6346fac669a`, over the 104
files of `bench/corpus/manifest.csv`: every widget of every file read twice
through `pdfacts.widgets` and once by a referee, a reading of the
dictionaries with pikepdf, independent of pdfacts, and the terminal fields
through `pdfacts.fields`, compared with the referee's count. The referee was
written clean-room, from the texts of issues #36, #38, #39 and #40,
`docs/conventions.md` and `tests/synthetic`, by an author who had not read the
source of pdfacts. pypdfium2 5.13.0, PDFium 153.0.7999.0, pikepdf 10.14.0,
QPDF 12.4.1.

```
104 files, 7934 widgets compared, 0 differences
equal per fact: object 7934, rect 7934, annotation_flags 7934, field_ref 7934, name 7934, partial_names 7934, type 7934, field_flags 7934, label 7934, value 7934, default_value 7934, options 7934, in_form 7934, states 7934, state 7934, export_value 7934, border_colour 7934, background_colour 7934, border 7934
known object 7934, rect 7934, annotation_flags 7934, field_ref 7934, name 7934, partial_names 7934, type 7934, field_flags 7934, label 6517, value 509, default_value 38, options 5, in_form 7934, states 2840, state 2845, export_value 811, border_colour 3918, background_colour 3943, border 3308; in_form true 7934
buttons: checkbox 1665, radio 1180; states equal 2845, state equal 2845, export_value equal 2845
choice fields: 5; options equal 5
unknown: value invalid-in-file 61 (checkbox 54, pushbutton 7)
stable: 104 of 104 files
fields: 6960 in 75 files, equal to the referee's count in 104 of 104 files, 0 differing from a widget's field
```

| Part of the gate in `ROADMAP.md` | Shown by |
| --- | --- |
| widget rectangle, name, type, flags and label equal the file content for every widget in the corpus | the run above, "equal per fact": `rect`, `name`, `type`, `annotation_flags`, `field_flags` and `label` equal the referee's for 7934 of 7934 widgets |
| options and states of radio buttons and check boxes equal the file content | the run above, "buttons": `states`, `state` and `export_value`, the entry of `/Opt` for the widget, equal for 2845 of 2845 radio buttons and check boxes; the options of the 5 choice fields equal as well |
| object references are stable across repeated reads | the run above, "stable": both reads of `widgets()` and of `fields()` equal in 104 of 104 files; `object` and `field_ref` equal the referee's for 7934 of 7934 widgets |

The code of M2 last changed on `2cb16f1`, the commit the gate ran on; the
merge of #53, `ee420b1`, adds only the record of that run and has the tree of
`fa685c5`. `python3 tools/checks.py --all` passed on `fa685c5`: 1592 tests on
macOS arm64, and 1591 on Linux arm64 and on Linux x86_64, where one test that
needs macOS is skipped.

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
| Where QPDF looks for `startxref` (2026-09-28) | in the last 1054 bytes: one that begins 1054 bytes before the end is found; one that begins 1055 bytes before is not, and QPDF rebuilds the table, reading through that `startxref` where it follows the last object and warning that it was more than 1024 bytes before the end. It passes over a `startxref` without an offset, one glued to its offset and one whose offset other characters follow, for the one before, without a warning; a comment or a sign before the offset is taken |
| A header after other bytes (2026-09-28) | offsets count from the header: QPDF with recovery off, and PDFium, read such a file as written (review, round 6). QPDF takes the first `%PDF-` that a version follows; PDFium takes the first `%PDF-`, and after a `%PDF-` without a version it has to rebuild its table (review, round 7) |
| White space and comments QPDF reads (2026-09-28) | a vertical tab as white space around a `startxref`'s offset and inside an object header, and a comment or a plus sign inside an object header (`4 0 %c` and a line end before `obj`, `4 +0 obj`), with recovery off and no warning; PDFium reads the comment and the plus sign, and has to rebuild its table for the vertical tab (reviews, rounds 8 and 9) |
| An offset QPDF cannot hold (2026-09-28) | a `startxref` offset of 2^63 or more fails QPDF's reading, recovery on, with `overflow/underflow converting ... to 64-bit integer`; when it rebuilds a file, QPDF passes over such a `startxref` (review, round 7) |
| A file cut after an earlier `%%EOF` (2026-09-28) | read through the earlier revision's `startxref` when it lies within those 1054 bytes, without a warning; with a longer comment appended, the file is rebuilt from its objects, the later object of each number counting, a broken later copy too |
| Rebuilding from the objects (2026-09-28) | QPDF reads through a `startxref` that follows the last object, warning that `startxref` was more than 1024 bytes before the end of the file, where the table its offset names leads to a page tree: an offset cut short can name an earlier table. It places objects in object streams only through a cross-reference stream: of 20 corpus updates with object streams, cut before their cross-reference stream, 6 came back as the latest revision and 14 as neither revision, and a constructed page moved into a new object stream came back with its earlier media box (review, round 5). Fails for an encrypted file whose objects are in object streams: "root of pages tree has no /Kids array" |
| A file whose trailer is lost (2026-09-28) | rebuilt as unencrypted, the encryption dictionary no longer named: encrypted strings and streams stay as they are |
| Warnings while saving (2026-09-28) | a stream object is parsed when it is first used, at the latest when it is written: a wrong `/Length` can be reported by `save()` |
| XMP metadata on save (2026-09-28) | pikepdf writes the XMP anew to set the PDF version in it (`fix_metadata_version`, on by default): its bytes change in every corpus file with XMP, XMP it cannot parse becomes an empty packet, and a metadata stream it cannot decode fails the save (review, round 5). With the option off, the metadata keeps its content; QPDF writes a metadata stream without a filter |
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
| Unrounded values, macOS against Linux | paths and widgets identical for all 104 forms; glyphs, with host fonts ignored, identical for all 104 forms (M1 gate, 2026-09-29) |

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

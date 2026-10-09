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
| Paths and images (`GRAPHICS`) | proven | `tests/test_graphics_reader.py`, `tests/test_graphics_shapes.py`, `tests/test_graphics_cases.py`; on the corpus, 63472 paths and 1884 images on 467 pages, their numbers equal to PDFium's own for every page's content and every annotation's appearance; the M3 gate |
| Text: glyphs, words and lines (`TEXT`) | proven | `tests/test_text_rules.py`, `tests/test_text_reader.py`, `tests/test_text_cases.py`, `tests/test_text_angles.py`; on the corpus, 1103908 glyphs, 154208 words and 32496 lines on 467 pages, the glyphs holding PDFium's characters in 104 of 104 files, every value identical on macOS arm64, Linux arm64 and Linux x86_64; the M4a gate |
| Recovering a damaged file (`RECOVERY`) | proven | `tests/test_recovery.py`. Every corpus file with its end damaged six ways (`%%EOF` removed, its last byte cut, cut right after the offset of its last `startxref`, junk, Ctrl-Z or a vertical tab appended) recovered with the geometry of the original. The 32 files with an incremental update, cut 200 bytes into it: 30 refused as ending inside an update; two updates were shorter. Five corpus forms cut at 70 % and 95 %: one of the ten recovered, with the geometry and the form fields of the original; two refused as ending inside an update; seven refused, QPDF finding no `/Kids` in the root of the page tree. All five keep objects in object streams |
| Inspecting a file's structure, encryption and active content (`INSPECTION`) | proven | `tests/test_inspection.py`, `tests/test_inspection_checks.py`, `tests/test_backend_qpdf_structure.py`, `tests/test_backend_qpdf_active_content.py`; on the corpus, the `/Contents` of each of 467 pages as pikepdf reads it apart from pdfacts, its streams and its array, 7 of them indirect arrays, and each of 1287 actions, 1258 entries that name them, 38 XFA streams and 2 embedded files in the object the report names; every value identical on macOS arm64, Linux arm64 and Linux x86_64; the M5a gate |
| Decrypting a file (`DECRYPTION`) | proven | `tests/test_decryption.py`, `tests/test_passwords.py`; the 14 encrypted corpus files decrypted to the same bytes on three platforms, and read by pypdf and by PyMuPDF with the field names and rectangles of the original, 1038 widgets, on each page as many as pikepdf finds, and by pypdf with the 1020 names of the field trees, also after each wrote the file anew; in the 13 with a text field, a value set and read back by each; 82 faults introduced into the results, each refused, and the right result returned, also when written anew by pikepdf; the M5a gate |
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
revision, without a warning. #32 closed the gap in M2 (#43).

## Milestone M2: gate

Run on 2026-09-30 with `bench/gates/m2.py` on commit `2cb16f131acb9a87e1bf536292bbe6346fac669a`, over the 104
files of `bench/corpus/manifest.csv`: every widget of every file read twice
through `pdfacts.widgets` and once by a referee, a reading of the
dictionaries with pikepdf, independent of pdfacts, and the terminal fields
read twice through `pdfacts.fields`, compared with the referee's count. The referee was
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

The library under `src/` last changed with #52; the gate's code last changed
on `2cb16f1`, the commit the gate ran on. The merge of #53, `ee420b1`, has the
tree of `fa685c5`, which differs from `2cb16f1` only in the commit named in
the record above. `python3 tools/checks.py --all` passed on `fa685c5`: 1592
tests on macOS arm64, and 1591 on Linux arm64 and on Linux x86_64, where one
test that needs macOS is skipped.

## Milestone M3: gate

Run on 2026-10-01 with `bench/gates/m3.py` on commit
`24caf1e784e0221f709a55a17939a9aaf82eefb8`, on macOS arm64, over the 104 files of
`bench/corpus/manifest.csv`: the paths and images of every page read twice
through `pdfacts.paths` and `pdfacts.images`, each time from the file opened
anew, and their numbers compared, for each page's content and each
annotation's appearance, with PDFium's own count of path and image objects,
forms expanded, read with pypdfium2 apart from pdfacts. pypdfium2 5.13.0,
PDFium 153.0.7999.0, pikepdf 10.14.0, QPDF 12.4.1.

```
104 files, 467 pages, 2960 annotations with facts
page: paths 58665, images 1884, line 12965, rectangle 43204, quad 119, polyline 1461, curves 8401
appearance: paths 4807, images 0, line 47, rectangle 2988, quad 0, polyline 800, curves 1019
unknown: appearance line_width unreadable 99, page fill_colour unreadable 638, page line_width unreadable 4, page stroke_colour unreadable 11
stable: 104 of 104 files
PDFium's own count: equal in 104 of 104 files; not compared: 0 pages, 0 annotations
```

The annotations with facts are those whose appearance holds a path or an
image; PDFium gives objects for 2972 annotations, 12 of them text alone. The
unknown values are those that "Paths and images" in `docs/conventions.md`
names, as their details say: the 649 colours, "a pattern colour or colour space
is set here, and PDFium reports a colour of its own", and the 103 line widths,
"the transform scales the line width unevenly". Since the colours are judged
path by path (2026-10-06), 5 fill colours of the corpus are unknown, "a
pattern colour is set here, and QPDF does not confirm the colour PDFium
reports for the fill", and no stroke colour (measured).

| Part of the gate in `ROADMAP.md` | Shown by |
| --- | --- |
| the rules that turn path segments into lines, rectangles, quads and curves are documented | `docs/conventions.md`, "Paths and images": the five shapes, the first rule that applies, the four corners, and when two numbers are one |
| and covered by constructed cases | `tests/test_graphics_shapes.py`, 34 tests of the rules on segments as given, the tolerance on both sides of it and growing with the numbers among them. `tests/test_graphics_cases.py`, `test_what_every_graphics_case_draws` over the 15 shape cases of `cases.GRAPHICS`, each drawn by a content stream and read through PDFium: `re`, `m l l l h` and `m l l l l h`, each a rectangle; a line; a rectangle turned by 90 degrees, still a rectangle, and a sheared one, a quad; a trapezoid; a corner 2^-11 off, still a rectangle, and 2^-6 off, a quad; an L and an open polyline; curves drawn with `c`, `v` and `y`; two rectangles in one path |
| constructed cases with nested transforms are correct | the same test over four cases: forms nested two deep, each with its `/Matrix` and a `cm` before it; one form drawn twice, the state restored in between; an appearance that draws a form, placed on its `/Rect`; an appearance turned by its `/Matrix` and placed unevenly. The points are the construction's, put into user space by hand |
| constructed cases with repeated images are correct | the same test over five cases: one image placed three times, scaled, turned and mirrored; one image in a form drawn twice; an inline image; an image in an appearance; paths and images in one drawing order, an appearance's last |
| checked apart from pdfacts | `test_the_ink_lies_where_the_case_says`: each of the 19 cases filled in black, rendered by PDFium, has its ink in the box of the points the case gives, within a pixel |

The tests ran with `python3 tools/checks.py --all` on `24caf1e`, the commit
the gate ran on: 2053 tests on macOS arm64, and 2052 on Linux arm64 and on
Linux x86_64, where one test that needs macOS is skipped. The commit that
records the run changes documents only.

## Milestone M4a: gate

Run on 2026-10-02 with `bench/gates/run.py m4a` on commit
`f36993a2c4c3898b2b30ffbdfa4bf9fed5c09951`, over the 104 files of
`bench/corpus/manifest.csv`: the glyphs, words and lines of every page read
through `pdfacts.glyphs`, `pdfacts.words` and `pdfacts.lines` after the
runtime installed the font policy, every value of every fact put into a
digest of each, and the characters the glyphs hold compared with PDFium's
own, read with pypdfium2 apart from pdfacts. Every platform reported
pypdfium2 5.13.0, PDFium 153.0.7999.0, pikepdf 10.14.0, QPDF 12.4.1 and the
font policy embedded-only.

| Platform | `uname -m` | Python | Files read | Pages | Glyphs | Words | Lines | Seconds |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| macOS arm64, natively | arm64 | 3.13.5 | 104 | 467 | 1103908 | 154208 | 32496 | 74.7 |
| Linux arm64, container `python:3.13.5-slim` | aarch64 | 3.13.5 | 104 | 467 | 1103908 | 154208 | 32496 | 88.3 |
| Linux x86_64, container `python:3.13.5-slim`, emulated on an arm64 host | x86_64 | 3.13.5 | 104 | 467 | 1103908 | 154208 | 32496 | 178.3 |

| Compared on all three platforms | Files identical |
| --- | --- |
| glyphs, every value of every glyph | 104 of 104 |
| words | 104 of 104 |
| lines | 104 of 104 |
| the gate's tallies | 104 of 104 |

The gate printed these lines on every platform:

```
104 files, 467 pages: 1103908 glyphs, 154208 words, 32496 lines
unknown: none
not given: glyphs not upright 2522 in 37 files, annotations with text in their appearance 48 in 19 files
left out by PDFium as drawn again: text objects 49 in 1 files, over invisible text 0 in 0 files
new lines inside one text object: after white space, forward 656 in 29 files, back 8 in 2 files; after another glyph, forward 280 in 11 files, back 0 in 0 files
PDFium's own count of characters: equal in 104 of 104 files; not compared: 0 pages
```

No file failed. On every page the glyphs hold PDFium's characters one after
another, and none is left over; no value of a glyph is unknown. Seconds are
the time the gate script took to read the corpus.

A run on three platforms before (#78) found one glyph whose direction
differed in its last bit between macOS and Linux: it came from the C
library's `atan2`. Since then a glyph's direction, and the vector along it
that words and lines take, are computed in decimal arithmetic and rounded
once, the same on every platform; with that, the directions of two other
glyphs changed by a bit on every platform, and no word or line changed.

### What the rule set is known to get wrong, counted on the corpus

The kinds are those that "Glyphs, words and lines" in
`docs/conventions.md` names under "Known to go wrong" and "Not given". The
new lines are counted where they begin inside one text object, PDFium's, one
for each operator that shows text, which draws one run along one baseline. The gate does not tell a line split wrongly
from a column set apart rightly, so each count of new lines is the most that
can be wrong inside one text object; new lines between two text objects are
not counted.

| Known to go wrong, or not given | Counted on the corpus |
| --- | --- |
| a space that `Tw` widens by 0.8 of the size or more; a run of spaces PDFium gives as one character, with a gap as wide after it; words a `TJ` spaces as far apart after a space | new lines inside one text object after white space, forward: 656 in 29 files |
| a space 0.8 of the size wide or more at the next character's origin | new lines inside one text object after white space, back: 8 in 2 files |
| letters that `Tc` spaces by 0.8 of the size or more; words a `TJ` spaces as far apart | new lines inside one text object after another glyph, forward: 280 in 11 files; after another glyph, back: none |
| text drawn again where PDFium leaves it out | 49 text objects that paint, in 1 file, none of them over invisible text. Characters left out of a text object that keeps others are not counted: PDFium gives no count of a text object's characters |
| not given: words and lines of text that is not upright | 2522 glyphs in 37 files |
| not given: text in annotation appearances | 48 annotations in 19 files |

What makes the gaps was measured with the scripts of #76 (third comment).
With every `Tw` set to 0, 579 of the 656 new lines after white space,
forward, remain; with the numbers of every `TJ` dropped, 551; with `Tw` and
`Tc` at 0 and the numbers of `TJ` dropped, 366. 298 of the 656 gaps are a
whole number of the white space's width. Of the 280 new lines after another
glyph, forward, 1 remains with the numbers of every `TJ` dropped. Looked at
by eye, these lines hold words of justified paragraphs, and fields and
columns of forms set apart.

| Part of the gate in `ROADMAP.md` | Shown by |
| --- | --- |
| constructed cases pin every rule of the rule set | `tests/test_text_rules.py`, every rule and threshold of "Glyphs, words and lines" on characters given directly. `tests/test_text_cases.py`, `test_what_every_text_case_holds` over the 31 cases of `cases.TEXT`, each drawn by a content stream and read through PDFium: PDFium's own space, content order, a ligature, a line-end hyphen; a gap of 0.1375 and of 0.15, of 0.7875 and of 0.8; a step back of 0.7875 and of 0.8; a baseline 0.7875 up and down, and 0.8 up and down; a step along and a step across in units of the later glyph's size; a space that ends a word and moves the pen; horizontal scaling; a glyph not upright passed over; upright within one degree and as the page is shown; invisible text; a line of white space alone; text off the page; and what is known to go wrong, with what is right beside it: a space `Tw` widens by 0.9 and by 0.7, a wide space at the next origin, letters `Tc` spaces by 0.8 and by 0.7, words a `TJ` spaces by 0.8, a run of spaces |
| checked apart from pdfacts | `test_the_ink_lies_in_the_words`: in 26 of the cases every glyph that paints is in a word; rendered by PDFium, every dark pixel lies in a word's box, within a pixel, and every word's box holds one |
| glyph, word and line facts of every corpus file identical on three platforms | the run above: 104 of 104 files for glyphs, words and lines, and for the gate's counts; `tests/test_text_angles.py` for the angles, worked out apart from pdfacts with exact rational arithmetic |
| what the rule set is known to get wrong, listed with counts from the corpus | the table above |

The tests ran with `python3 tools/checks.py --all` on `f36993a`, the commit
the gate ran on: 2376 tests on macOS arm64, and 2375 on Linux arm64
and on Linux x86_64, where one test that needs macOS is skipped. The commit
that records the run changes documents only.

## Milestone M5a: gate

Run on 2026-10-05 with `bench/gates/run.py m5a` on commit
`8703d77e037f6d1817e77fb2ae96535501c3cbc3`, over the 104 files of
`bench/corpus/manifest.csv`, after the runtime installed the font policy.
Every platform reported pypdfium2 5.13.0, PDFium 153.0.7999.0, pikepdf
10.14.0, QPDF 12.4.1 and the font policy embedded-only, and of the
dependency group `bench`, pypdf 6.19.0 and PyMuPDF 1.28.2.

| Platform | `uname -m` | Python | Files read | Pages | Findings | Faults | Seconds |
| --- | --- | --- | --- | --- | --- | --- | --- |
| macOS arm64, natively | arm64 | 3.13.5 | 104 | 467 | 2585 | 352 | 44.5 |
| Linux arm64, container `python:3.13.5-slim` | aarch64 | 3.13.5 | 104 | 467 | 2585 | 352 | 44.5 |
| Linux x86_64, container `python:3.13.5-slim`, emulated on an arm64 host | x86_64 | 3.13.5 | 104 | 467 | 2585 | 352 | 78.2 |

| Compared on all three platforms | Files identical |
| --- | --- |
| inspection: every value `pdfacts.inspect()` gives, the forms of `/Contents` and the findings counted | 104 of 104 |
| decryption: for the 14 encrypted files, the SHA-256 of what `pdfacts.decrypt()` wrote and what each library read; for the others, that they are not encrypted | 104 of 104 |
| recovery: for the 90 files not encrypted, the SHA-256 of what `pdfacts.recover()` wrote; for the others, that they are encrypted | 104 of 104 |
| faults: the outcome of each fault introduced | 104 of 104 |

The gate printed these lines on every platform:

```
104 files, 467 pages; 14 files encrypted, 14 of them with an owner password that is not the empty one
/Contents: stream 379, array 81, indirect-array 7, absent 0, other 0; as pikepdf reads them on 467 of 467 pages
active content: actions 1287, triggers 1258, xfa streams 38, embedded files 2; in the object named: 2585 of 2585
pypdf: 1038 widgets, as many on each page as pikepdf finds in 14 files, 1020 fields in the field tree; field names and rectangles equal after decryption in 14 files, after writing in 14; encrypted before and not after in 14; a value set and read back in 13, no text field in 1
pymupdf: 1038 widgets, as many on each page as pikepdf finds in 14 files; field names and rectangles equal after decryption in 14 files, after writing in 14; encrypted before and not after in 14; a value set and read back in 13, no text field in 1
decrypt(), faults refused: a widget moved 13 of 13, a stroke added 14 of 14, a page removed 14 of 14, a field left out 13 of 13, still encrypted 14 of 14, a warning 14 of 14; the right result returned in 14 of 14, written anew by pikepdf in 14 of 14
recover(), faults refused: a page removed 90 of 90, no /Size 90 of 90, cut short 90 of 90; the right result returned in 90 of 90, written anew by pikepdf in 90 of 90
files that failed a condition: 0
```

| Part of the gate in `ROADMAP.md` | Shown by |
| --- | --- |
| owner-password-protected corpus files are readable and writable by a third-party library after decryption, with field names and rectangles equal to those of the original file | `pdfacts.decrypt()`, given no password, wrote anew the 14 encrypted files, those pikepdf reads as encrypted, each with an owner password that is not the empty one. pypdf and PyMuPDF each read the field name and the rectangle of every widget, page by page: 1038 in the originals, on each page as many as pikepdf finds in its `/Annots`, and the same in the decrypted files and in the files each wrote anew from them; pypdf also read the same 1020 names of the field trees in each. Each read the originals as encrypted, and the decrypted files and its own as not. Each set the value of the first text field, wrote the file and read the value back, in the 13 files where pikepdf finds one; the fourteenth, `06`, has no widget |
| a page `/Contents` entry that is an indirect array is handled | `pdfacts.inspect()` gave the `/Contents` of all 467 pages: 379 streams, 81 arrays and 7 indirect arrays. pikepdf, read apart from pdfacts, found on each page the same form, the same streams, as many other entries and the same array |
| the verification inside each function detects a deliberately introduced fault | the QPDF backend's writing replaced by one that returns a result made wrong on purpose. `decrypt()` refused with `DecryptionFailedError` each of six faults on each encrypted file: a widget moved by one point and the last field of `/Fields` left out (neither in `06`, which has no widget and no field), a stroke added to the first page, the last page removed, the input returned still encrypted, a warning reported. `recover()` refused with `RecoveryFailedError` each of three on each of the 90 other files: the last page removed with the page count kept, the last `/Size` taken out, the file cut to half its length. `recover()` keeps a file's encryption, and what it writes for an encrypted file differs from one call to the next, so its faults go to the files not encrypted. Given the right result unchanged, and written anew by pikepdf as the faults that edit it are, each function returned it in every file |
| the active content report names the object in which each finding was made | pikepdf found each finding in the object the report names: the 1258 entries, each at its key in its owner, naming a value of its own of the kind of its action, written in the action's object; the 1287 actions, each found by an entry or by a `/Next` that names it in its order, or, where nothing names it, as a dictionary of its kind; the 38 XFA streams, where `/XFA` names them, in order; and the 2 embedded files, each a file specification with the streams its `/EF` names |

No file failed a condition, and none raised an error. Seconds are the time
the gate script took over the corpus, its decryptions, recoveries and faults
included.

`decrypt()` compares the fields as QPDF reads them since #102 (F7): a
result whose `/Fields` lost its last field is one of the faults it refused.

The tests ran with `python3 tools/checks.py --all` on `8703d77`, the commit
the gate ran on: 3224 tests on macOS arm64, and 3223 on Linux arm64 and on
Linux x86_64, where one test that needs macOS is skipped. The commit that
records the run changes documents only.

## Milestone M4b: gate

Run on 2026-10-09 with `bench/gates/run.py m4b` on commit
`58cca132df38cbbde5edd91fb63b0b50b1b28dda`, over the 104 files of
`bench/corpus/manifest.csv`, after the runtime installed the font policy.
Every platform reported pypdfium2 5.13.0, PDFium 153.0.7999.0, pikepdf
10.14.0, QPDF 12.4.1 and the font policy embedded-only. The gate reads the
glyphs, words and lines of `TextRules.V2` of every page through
`pdfacts.glyphs`, `pdfacts.words` and `pdfacts.lines`, looks PDFium's
characters up among the glyphs, and renders each page's text alone to check
the word boxes against its ink, for the boxes of `TextRules.V2` and for the
font descriptor's unscaled heights.

| Platform | `uname -m` | Python | Files read | Pages | Glyphs | Words | Lines | Seconds |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| macOS arm64, natively | arm64 | 3.13.5 | 104 | 467 | 1139030 | 155329 | 31950 | 100.8 |
| Linux arm64, container `python:3.13.5-slim` | aarch64 | 3.13.5 | 104 | 467 | 1139030 | 155329 | 31950 | 114.0 |
| Linux x86_64, container `python:3.13.5-slim`, emulated on an arm64 host | x86_64 | 3.13.5 | 104 | 467 | 1139030 | 155329 | 31950 | 196.8 |

| Compared on all three platforms | Files identical |
| --- | --- |
| glyphs, every value of every glyph | 104 of 104 |
| words | 104 of 104 |
| lines | 104 of 104 |
| the agreement of PDFium's characters with the glyphs | 104 of 104 |
| the word boxes judged against the rendering | 104 of 104 |

The gate printed these lines on every platform:

```
104 files, 467 pages: 1139030 glyphs, 155329 words, 31950 lines
unknown: glyph text unreadable 10, line text unreadable 7, word text unreadable 10
pages unknown as a whole: 0 in 0 files
PDFium's characters, among the glyphs of V2: same text 1101606, line-end hyphen marker 2162, soft hyphen read as a hyphen 71, same text under NFKC 57, V2 text unknown 10, same first character 2, other text at the place 0, no glyph at the place 0
glyphs of V2 that are no white space: a PDFium character at the place 993465, drawn again 0, invisible 43, other 12
counted apart: PDFium: text unknown 0, PDFium: origin unknown 0, V2: white space 145500, V2: origin unknown 0, pages without PDFium's characters 0, V2: text unknown unreadable 10
pages measured 467, pages not mapped 0, pages where D groups otherwise 0
word boxes of V2, one em at the least: 155329 words, 30 not horizontal, 6 outside the page, 155293 measured; 3829 all invisible, not judged; judged 151464: correct 151332, without ink of their own 30, failing vertically 47, failing horizontally 65, into another line 1173; unattributed ink: 2322 components, 273449 pixels
word boxes of D, the descriptor's height: 155329 words, 30 not horizontal, 6 outside the page, 155293 measured; 3829 all invisible, not judged; judged 151464: correct 151297, without ink of their own 30, failing vertically 82, failing horizontally 65, into another line 1170; unattributed ink: 2322 components, 273449 pixels
good in V2 and not in D: 35, good in D and not in V2: 3
word boxes correct: 151332 of 151464 (99.91%), at least 99%: yes
the descriptor's height does better: no
```

No file failed, no page was unknown as a whole, and none of PDFium's
characters was at a place where V2 has no glyph. Seconds are the time the
gate script took over the corpus.

The time gate, `bench/gates/m4b_time.py`, run on the host on the same
commit over the same corpus, gave a median of 0.0455 s per file for the
reference engine (PyMuPDF), 0.0645 for `TextRules.V2` and 0.0909 for
`TextRules.V1`: the ratio of the medians for V2 is 1.418, at most the 1.5
the gate allows, the median of the ratios per file 1.612. It was run immediately
after the three-platform run, OrbStack still up and its containers idle;
on a quiet machine the same method gave 1.398 and 1.404 (F5, #132), and
1.474 and 1.481 before it.

### What does not agree with PDFium, by category

PDFium's characters are looked up among the glyphs of V2 at the same
origin, within 0.01 pt, and the categories are those of the gate. The
glyphs V2 has and PDFium does not give are counted apart, white space
among them: PDFium's text interface leaves out about 12% of the space
glyphs ("What the engines provide"), which is why the second rule set
exists.

| Category | Counted on the corpus |
| --- | --- |
| the same text at the same origin | 1101606 characters in 103 files |
| PDFium's U+0002, the marker it gives for a line-end hyphen, where V2's glyph is `-` or a soft hyphen | 2162 characters in 66 files |
| a soft hyphen, U+00AD, where PDFium reads `-` | 71 characters in 5 files |
| the same text once composed (NFKC), ligatures among them | 57 characters in 5 files |
| the glyph's text unknown where the character's is read | 10 characters in 1 file, the font mapping their codes to no text |
| the first character of the glyph's text alone where PDFium expanded a ligature into several at one origin | 2 characters in 1 file |
| another text at the place; no glyph at the place; the character's or the glyph's origin unknown | none |
| of the glyphs of V2 that are no white space: a character of PDFium at the place | 993465 glyphs |
| of the glyphs of V2 that are no white space: invisible text | 43 glyphs in 1 file |
| of the glyphs of V2 that are no white space: drawn again where PDFium leaves it out | none |
| of the glyphs of V2 that are no white space: other | 12 glyphs in 2 files |
| white space glyphs of V2, counted apart | 145500 |
| glyphs of V2 whose text is unknown, unreadable, counted apart | 10 in 1 file |

### The words whose boxes fail the rendering

Of the 155329 words, 30 are not horizontal and 6 lie outside the page, so
their ink cannot be measured; 3829 hold invisible glyphs only and are not
judged. Of the 151464 judged: 151332 are correct, their own ink reaching
out of their box by no more than 0.1 em on every side; 30 words of visible
glyphs have no ink of their own, and are judged and not correct; 47 fail
across the baseline, 65 along it, and 1173 hold the ink of another line
deeper than 0.1 em, which the gate counts apart. The boxes of the font
descriptor's unscaled heights judged the same words 151297 correct, 82
failing across the baseline: 35 words are good in V2 and not with them,
3 the other way, so the descriptor's height does not do better. 2322
components of ink, 273449 pixels, belong to no measured word. The receipts of the run, `bench/out/m4b-*.json`, name the files
whose words fail and how many each way; the words themselves the corpus
alone can name.

| Part of the gate in `ROADMAP.md` | Shown by |
| --- | --- |
| word boxes checked against rendering correct for at least 99% of words | 151332 of 151464 (99.91%) on every platform; the boxes of the descriptor's unscaled heights do not do better (151297 of 151464), so the question of ROADMAP.md does not go back to the maintainer |
| median time per file at most 1.5 times the reference engine's | `bench/gates/m4b_time.py`: the ratio of the medians 1.418 (1.398 and 1.404 on a quiet machine, F5); the median of the ratios per file 1.612. `TextRules.V1` took 2.000 for the same corpus, read as the ratio of the medians |
| PDFium's characters found among V2's glyphs, what does not agree listed by category | the table above: every character of PDFium was found, none at a place where V2 has no glyph and none with another text there; 2162 line-end hyphen markers, 71 soft hyphens and 57 NFKC differences among them |
| glyph, word and line facts of V2 identical on three platforms | the run above: 104 of 104 files for glyphs, words and lines, for the agreement and for the word boxes judged |

The tests ran with `python3 tools/checks.py --all` twice: on `de82c99`,
whose tree is that of `58cca13`, the commit the gate ran on, and on
`6b742ee`, the commit that records the run, which differs from it in
documents only. Each gave 3920 tests on macOS arm64, and 3919 on Linux
arm64 and on Linux x86_64, where one test that needs macOS is skipped.

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

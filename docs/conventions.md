# Conventions

Status: draft. These become binding with milestone M1. Until version 1.0 a
convention changes only with the maintainer's consent, and every change is
recorded in [CHANGELOG.md](../CHANGELOG.md).

Each convention exists because the alternative produced a wrong or unstable
result in measurements on 109 files (104 official German forms, 474 pages,
about one million glyphs).

## Coordinate spaces

| Space | Definition |
| --- | --- |
| user | PDF default user space of the page, as written in the file |
| display | the visible page: media box intersected with crop box, rotation applied, origin bottom left, y upwards |

Facts are stored in user space. `PageGeometry` provides the transform to display
space and back. A library that writes into the file needs user space; a viewer
overlay needs display space.

There is no third space with y pointing downwards. A rendered bitmap carries
the transform from display space to its own pixels.

Reason: on rotated pages, mixing unrotated coordinates with the rotated page
size gave positions that did not match the rendering.

With the visible box `(x0, y0, x1, y1)`, W = `x1 - x0` and H = `y1 - y0`, the
transform to display space is:

| Rotation | x' | y' | `Matrix(a, b, c, d, e, f)` | Displayed size |
| --- | --- | --- | --- | --- |
| 0 | x - x0 | y - y0 | `(1, 0, 0, 1, -x0, -y0)` | W x H |
| 90 | y - y0 | x1 - x | `(0, -1, 1, 0, -y0, x1)` | H x W |
| 180 | x1 - x | y1 - y | `(-1, 0, 0, -1, x1, y1)` | W x H |
| 270 | y1 - y | x - x0 | `(0, 1, -1, 0, y1, -x0)` | H x W |

A `Matrix` means what a PDF matrix means: x' = a·x + c·y + e,
y' = b·x + d·y + f. The matrices hold only 0, 1 and -1 besides the offsets, so
the transform carries no rounding from trigonometry. Display space is measured
in user space units: `UserUnit` does not scale it, because the engine does not
scale what it renders either (measured).

Every coordinate is a finite number. A rectangle puts its corners in order,
because a file may write them reversed.

## Page boxes

The page bounding box is read with a call that resolves inheritance from the
page tree. `FPDFPage_GetMediaBox` does not resolve it;
`FPDF_GetPageBoundingBox` does.

`UserUnit` is read through QPDF. PDFium has no accessor for it. When it cannot
be read, the value is unknown, not 1.

## Glyph boxes

| Box | Definition |
| --- | --- |
| advance box | from the origin along the writing direction by the advance width; height from the font's ascent and descent |
| ink box | tight bounds of the glyph outline |

Height from the font's ascent and descent placed the top edge within 0.5 pt of
the reference for 98.6% of glyphs. The engine's own loose box reached 89.7%.

## Word and line boxes

The union of the advance boxes of the glyphs that are not white space. Leading
and trailing spaces do not extend a box.

Reason: PDFium omits about 12% of space glyphs, so a box that includes spaces
depends on which spaces the engine happened to keep.

## Writing direction

Taken from the glyph matrix. `FPDFText_GetCharAngle` has the opposite sign.

## Text

The raw characters are kept. Normalized text is provided next to them:

| Engine output | Normalized |
| --- | --- |
| U+0002 for a hyphen at the end of a line | U+002D, flagged as a line-end hyphen |
| a ligature expanded into several characters at one origin | kept as one cluster |

## Appearance streams

Path and image facts include what annotation and widget appearance streams
draw. Each fact states whether it came from page content or from an appearance.

Reason: without appearance streams only 54% of the check boxes in the corpus
were found.

## Fonts

Fonts installed on the host are ignored. Only fonts embedded in the file and
the engine's built-in substitutes are used. This is the only policy in M1; the
parameter exists so that another policy can be added without changing the
interface.

PDFium's font configuration belongs to the process, so one process has one
policy. It is installed before the first document is opened, and
`engine_info()` names it in `font_policy`, because it changes the facts.

Reason: 28 of 104 files gave different glyph facts on macOS and Linux. With
host fonts ignored, all 104 were identical on macOS arm64, Linux arm64 and
Linux x86_64, compared at 0.001 pt.

## Object identity

An annotation is identified by page index and its index in the page's
annotation array. The object number comes from QPDF at the same index. PDFium
does not expose object numbers. The mapping held for all 7935 widgets in the
corpus.

The corpus contains no damaged files, and on a damaged file the two engines may
recover different page trees or annotation lists. Therefore the mapping is
checked at run time:

| When | Check |
| --- | --- |
| on open | both engines report the same number of pages |
| before a value from QPDF is used for a page or an annotation | the object at the same position is the same in both engines. For a page, PDFium not exposing object numbers, that means the same visible box, within 0.001, and the same rotation. QPDF's visible box is its media box, US letter where it has none it can use, intersected with its crop box, corners in order; its rotation is taken modulo 360, and a value that is not a multiple of 90 counts as 0 |

When a check fails, the values that depend on it are unknown and the reason is
stated. Facts read through PDFium are returned as usual.

Reason: a wrong object number is worse than none. A later change to the file
would address the wrong object.

## Three answers

| Answer | Meaning | How it is expressed |
| --- | --- | --- |
| absent | the check was done and the file does not contain it | `None`, or an empty collection |
| unknown | the check could not be done on this file | `Unknown(reason)` |
| unsupported | this version of the library cannot do the check at all | the capability table; calling the feature raises an error |

A check that could not be carried out is unknown. It is never reported as
absent, and a value is never made up to fill the gap.

A single unknown value does not discard the facts around it.

An unknown value is `pdfacts.Unknown`, with one of three reasons:

| Reason | Meaning |
| --- | --- |
| `engines-disagree` | the two engines describe the object differently |
| `unreadable` | the value could not be read from the file |
| `invalid-in-file` | the file contains a value that the PDF specification does not allow |

An unknown value refuses to be used as a boolean: `if value:` raises
`TypeError`, so that it cannot silently count as true. Test it with
`isinstance(value, Unknown)`.

The capability table, `pdfacts.capabilities()`, lists every capability and
whether this version has it. A capability is True only once it is implemented.

## Errors

Every error is a `PdfactsError`. Its attributes are given as keywords and are
readable under the same names.

| Error | Parent | Attributes | Raised when |
| --- | --- | --- | --- |
| `InputError` | `PdfactsError` | - | never directly |
| `EmptyInputError` | `InputError` | - | the input has 0 bytes |
| `NotPdfError` | `InputError` | - | there is no `%PDF-` in the first 1029 bytes |
| `DamagedFileError` | `InputError` | `detail` | there is a header, and the engine cannot load the file |
| `PasswordRequiredError` | `PdfactsError` | `password_was_given` | a password is needed, or the one given is wrong |
| `LimitError` | `PdfactsError` | `limit`, `allowed` | never directly |
| `LimitExceededError` | `LimitError` | `found` | a limit was exceeded |
| `LimitUncheckedError` | `LimitError` | `reason` | a limit was set and could not be checked |
| `RecoveryFailedError` | `PdfactsError` | `detail` | recovery did not produce a usable file |
| `UnsupportedError` | `PdfactsError` | `capability` | a feature was asked for that this version does not have |
| `ClosedError` | `PdfactsError` | - | a document or page was used after it was closed |
| `ConfigurationError` | `PdfactsError` | - | the runtime was configured too late, or twice with different values |
| `EngineError` | `PdfactsError` | `engine`, `detail` | an engine failed in a way none of the above describes |

The number 1029 is the 1024 bytes that PDFium accepts before the header
(measured: it refuses 1025) plus the five bytes of `%PDF-`.

A limit that could not be checked does not count as passed; that is why
`LimitUncheckedError` exists.

`MemoryError` is never caught and never wrapped.

Errors can be pickled with their attributes and their notes, so that they
cross process boundaries intact.

`Unknown`, `UnsupportedError` and `require()` accept a member of their enum
or its value, for example `"rendering"`, and reject anything else with
`ValueError`. The `reason` of `LimitUncheckedError` has to be an `Unknown`.

## Damaged files

`open` does not change what it reads. A file that the engine cannot load is
reported as damaged. Recovery is a separate, explicit step: the caller asks
for recovered bytes and opens those.

| Case | Result |
| --- | --- |
| recovery succeeds | new bytes; warnings raised during recovery are returned with them |
| recovery fails | an error; never an empty document |

Reason: recovery rewrites the file. Object numbers may change and the bytes and
their hash do change. Facts read from recovered bytes describe the recovered
file, so the caller has to hold those bytes.

A recovery can lose pages and still succeed, and the library cannot tell: the
engine that rebuilds the file has repaired its page tree before anything can
be read, so the number of pages the damaged file was meant to have is not
known. The result states its own page count; a caller who knows how many
pages to expect compares the two. Whether a partly recovered file is
acceptable is the caller's decision.

These count as files the engine cannot load:

| Case | Reason |
| --- | --- |
| the file does not end with `%%EOF` and white space: there is no `%%EOF`, or anything but white space follows the last one | a file cut short can end in an earlier revision's cross-reference table, which the engines then read without a word; so can a file with bytes appended |
| PDFium can read the file only by rebuilding its cross-reference table | that is a repair |
| PDFium finds no page, or no page dictionary for one of the pages it counts | an empty page tree, a missing one and one that names no object look the same to PDFium; the count comes from `/Count` and can promise pages that are not there |

A page counts as damaged when it is read, and reading it raises
`DamagedFileError` while the other pages are read as usual, when PDFium's
visible box for it is not finite (the file writes a real number beyond what
PDFium holds) or has no area, or when the boxes the file writes leave no
area: a flat crop box, or a media box that is flat or that, intersected with
the crop box, has no area. PDFium puts in US letter for a flat media box,
ignores a flat crop box and answers `(0, 0, 0, 0)` for a crop box outside the
media box; none of these is a box the file writes. The file's boxes are read
through QPDF, so the flat cases are seen only where QPDF can read the page.

A known gap (measured): PDFium can read a box as flat where the file writes
one with an area. It reads an integer outside -2^31 to 2^32 - 1 as 0, and one
above 2^31 - 1 when it has a plus sign; it holds reals in 32 bits, so
`100.000001` becomes `100`. QPDF reads such a box as written. Where the
engines then disagree, the page's values from QPDF are unknown; the page is
not counted as damaged, and its geometry is what PDFium makes of the box: US
letter for a flat media box, the media box for a flat crop box, neither of
which the file writes. QPDF reads integers from -2^63 to 2^63 - 1; outside,
it drops the page, the page counts differ, and every value from QPDF is
unknown.

Measured: every one of the 104 intact corpus files ends with `%%EOF` and white
space, has a cross-reference table PDFium accepts as written, and has a page
dictionary for every page. A linearized corpus form cut at 60 % opened in
PDFium, which rebuilt its table. PDFium looks for `startxref` in the last 4096
bytes, so more than about 4 KB of white space after `%%EOF` makes it rebuild
the table, and the file counts as damaged.

## Limits

The library checks what it can check between two engine calls:

| Limit | Checked |
| --- | --- |
| size in bytes | before opening |
| number of pages, number of objects | after opening, before any page is read |
| number of items per page | while facts are collected |
| deadline | between pages |
| pixels of one rendering | before the engine is called |

Time and memory cannot be enforced from inside a running engine call. They are
the caller's responsibility; a separate process per document is the
recommended way.

The numbers are passed in by the caller. The library has no built-in defaults.

## Rendering

A page or a region of it is rendered at a given scale to grey or RGB. The
result is raw bytes with width, height and row length. No type of an imaging
library appears in the public interface.

The region is given in user space, like every fact, and is cut to the
displayed page; the scale is pixels per user space unit. Pixel coordinates
count columns and rows from the top left corner. A bitmap of the region
`(rx0, ry0, rx1, ry1)` in display space at scale s carries `display_to_pixel`,
`Matrix(s, 0, 0, -s, -rx0·s, ry1·s)`: column = (x - rx0)·s, row = (ry1 - y)·s.
The engine decides how it rounds the size of the bitmap and the edges of the
region, and it places points in 32-bit floating point. On a page of up to
2^20 pixels a side, its own transform to the pixels differs from
`display_to_pixel` by up to about one pixel, and where the page, the region
and the scale give whole pixels by no more than one step of those floats, 1/8
pixel at most; the edges of what it fills may snap to whole pixels, further
off. On larger pages the difference grows with the page's size in pixels, to
tens of pixels near the 2^31 pixels a side that the engine addresses
(measured: 4 pixels at 4·10^8, 28 at 2·10^9).

## Digest

The digest is a tool under `bench/`. It is not part of the public interface.
It serves two purposes: comparing platforms, and noticing what an engine
upgrade changes.

Whether values have to be rounded before hashing is not known yet.

| Measurement | Result |
| --- | --- |
| PDFium, unrounded values, Linux arm64 against Linux x86_64 | glyph, path and widget digests identical for all 104 forms |
| PDFium, unrounded values, macOS against Linux | not measured |
| PDFium, values rounded to three decimals, host fonts ignored, macOS against Linux | identical for all 104 forms |
| the reference engine, word coordinates rounded to three decimals, macOS against Linux | identical for 83 of 104 forms |

The last row shows that rounding does not make nearly equal values hash
equally: two values on either side of a rounding boundary still differ.

The missing measurement is part of the M1 gate, and its result decides:

| Result | Digest |
| --- | --- |
| unrounded digests are identical on all three platforms | exact values are hashed |
| they differ | platforms are compared fact by fact with a tolerance, not by hash |

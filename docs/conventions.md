# Conventions

Status: binding since milestone M1 was done on 2026-09-29. Until version 1.0 a
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
| advance box | along the writing direction, the extent of PDFium's loose box: as a rule from the origin, or from the ink where it begins before, to the end of the advance, or of the ink where it reaches further; for some glyphs, mostly hyphens in fonts the file embeds, the ink alone. Across, from the font's descent to its ascent at the glyph's size |
| ink box | tight bounds of the glyph outline |

Height from the font's ascent and descent placed the top edge within 0.5 pt of
the reference for 98.6% of glyphs. The engine's own loose box reached 89.7%.

PDFium gives no advance width that can be relied on, so the advance box takes
the extent of its loose box along the writing direction (decision of the
maintainer, 2026-10-01). Asked with the character, `FPDFFont_GetGlyphWidth`
gives for the characters of an expanded ligature 0, or the width of another
code, and for a CID font without `/ToUnicode` its default width instead of
the width `/W` gives; the step from one origin to the next holds the
character and word spacing and the adjustments of `TJ`. Of 988690 upright
characters of the corpus that are no space, 79076 have a loose box that ends
at their ink.
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

## Glyphs, words and lines

A page's glyphs, words and lines are facts in user space, in content order:
by text object, in the order the page's content draws them, forms where they
are drawn; within one object, as PDFium's text page gives its characters. The
characters PDFium adds of its own, spaces and line breaks, are none of them.
Nothing is clipped: text outside the visible box is given as well.

A glyph is one character of the content, or several one after another at one
origin, as PDFium gives the characters of a ligature it expanded, an accent
at its letter, or the two halves of a character beyond U+FFFF. A glyph of
white space always stands alone: white space is a character Python's
`str.isspace()` calls so, a space, a tab, a no-break space, a line end and
the like, of a code PDFium maps. A glyph keeps the raw characters ("Text");
its values:

| Value | Rule |
| --- | --- |
| text | the characters as PDFium maps the codes, U+0002 for a hyphen at the end of a line; unknown where PDFium finds no character for a code, or gives half of a surrogate pair alone |
| direction | of the glyph matrix, PDFium's matrix scaled by the font size: the angle of its x axis, a font size below 0 turning it half round |
| size | the font size across the baseline: the glyph matrix's area over its length along the baseline, so that horizontal scaling and shear leave it as it is |
| box | the advance box ("Glyph boxes"), a quadrilateral: the start of the baseline at the descent, its end at the descent, its end at the ascent, its start at the ascent; of several characters, the extent of all their loose boxes |
| upright | whether the baseline runs left to right as the page is shown, its rotation applied, within one degree |
| render mode | 0 to 7 as `Tr` sets it; 3 and 7 paint nothing. Invisible text is given like any other |

The direction, size, render mode and font of a glyph of several characters
are its first character's; its box holds all their loose boxes along the
baseline; it is a line-end hyphen where one of them is.

Words and lines are made of the upright glyphs whose box is known; the others,
white space among them, are passed over: they move no pen and end no word. For each glyph that takes part after the
first, the step from where the glyph before that took part ended along its
baseline to this glyph's origin is measured in units of this glyph's size,
along its baseline and across it:

| Step | Then |
| --- | --- |
| across, 0.8 or more either way | a new line begins |
| along, 0.8 or more forward, or 0.8 or more back | a new line begins |
| along, from 0.15 up to 0.8 forward | a word ends |
| along, less than 0.15 forward, or less than 0.8 back | the glyph belongs to the word |

A glyph of white space ends a word and belongs to none; it moves the pen like
any glyph. A line without a word is none. A word's text is its glyphs' text
with U+0002 written as "-", the normalized text of "Text"; a line's is its
words' text with one space between two. Either is unknown where the text of
one of its glyphs is. A word joins nothing across a line-end hyphen. A word's
box holds its glyphs' boxes, a line's its words' boxes ("Word and line
boxes").

Reason: on the corpus, these rules give 97.6 % of the words and 92.8 % of
the lines of the engine the first consumer uses now, 93.6 % of the lines that
are upright as shown; the line breaks PDFium adds give 54.8 % of the lines,
since PDFium joins the text of separate columns on one baseline into one
line. A step back is no gap: an italic glyph's ink can reach past its
advance. The thresholds follow the approach of MuPDF's structured text
(`docs/provenance.md`); each is pinned by a constructed case.

Known to go wrong: a space that `Tw` widens by 0.8 of the size or more begins
a new line, since PDFium's loose box does not hold `Tw`; so does a space 0.8
of the size wide or more that stands at the next character's origin, its box
reaching past it, so that the next step is one back; and letters that `Tc`
spaces by 0.8 of the size or more each stand on a line of their own. Not
given: words and lines of text that is not upright, and text in annotation
appearances.

## Appearance streams

Path and image facts include what annotation and widget appearance streams
draw. Each fact states whether it came from page content or from an appearance.

Reason: without appearance streams only 54% of the check boxes in the corpus
were found.

## Paths and images

Paths and images are facts in user space, like every fact. A path is a list of
subpaths. A subpath is a move and the segments that follow it, lines and
curves, a curve given by its two control points and its end; it ends before
the next move, or with a segment that closes it. `h` ends a subpath, and a
line or a curve after it begins a new one where the closed subpath began (ISO
32000-1, 8.5.2.1); PDFium gives no move there, so the new subpath's move is
put at that point.

Each subpath has a shape, by the first of these rules that applies to its
segments in user space:

| Shape | When |
| --- | --- |
| curves | a curve is among its segments |
| line | a move and one line, the line not closing the subpath |
| rectangle | a move and four lines, the last line ending at the move, closed or not, the edges alternating between keeping y and keeping x; its `rect` is the smallest rectangle that holds the four corners |
| quad | a move and four lines, the last line ending at the move, that are no rectangle; its `quad` holds the four corners in the order they are drawn |
| polyline | anything else, a lone move included |

The four corners are the point of the move and the ends of the first three
lines; the edges run from each corner to the next and from the fourth back to
the first.

Two numbers are one where they differ by at most 0.001 plus a millionth of the
larger magnitude of the two. Two points are one where their x and their y are;
an edge keeps x or y where its two ends' x or y are one. A caller that needs
other rules keeps the segments.

Reason: PDFium turns `re`, `m l l l h` and `m l l l l h` into the same five
points, the last segment closed, so no rule can tell them apart. PDFium keeps
coordinates as 32-bit floats. Near 14400 units, the largest page ISO 32000-1
recommends (Annex C), they lie about 0.001 apart; anywhere, at most 2^-23 of
the magnitude apart, and coordinates can lie far beyond the page.

`paths(document, page_index)` and `images(document, page_index)` give what
PDFium interprets for the page, in drawing order: its content, each form
where it is drawn, then the normal appearance of each annotation in `/Annots`
order. They give it whether or not a rendering shows it: hidden and NoView
annotations, closed pop-ups and widgets too. Paths and images share one
drawing index. A fact from an appearance carries the annotation's reference
and its `/F`, 0 where it has none; a fact from the content carries neither.
The values come from PDFium, which gives each object's points in its own
space and its matrix composed through the forms that hold it; pdfacts
composes the matrices in 64-bit floats. The deadline is checked when either
function begins.

An appearance's graphics are used only where the engines agree on it. QPDF
selects the normal appearance PDFium would draw (`/AS`, else `/V`, else the
`/V` of `/Parent`, else `/Off`); the text PDFium gives for the annotation's
normal appearance must equal that stream's data, each byte read through
PDFDocEncoding as PDFium reads it, and the flags must agree, modulo 2^32, an
absent `/F` as 0. An `/F` that is not an integer is not compared, and one
that is not an integer from 0 to 2^32 - 1 leaves the facts' flags unknown,
`invalid-in-file`. Where the engines do not agree,
every fact of the annotation is unknown, `engines-disagree`. Where QPDF selects
no appearance, PDFium must draw none; once the page was rendered, PDFium draws
appearances it generated for annotations that have none, and those
annotations are then unknown. Where QPDF cannot tell which appearance PDFium
draws, and where QPDF cannot decode the appearance stream, the annotation's
facts are unknown. An annotation whose facts are unknown is given as one path
and one image, each of its own drawing index, with the annotation's reference
and every value unknown with the reason, its flags included. Where PDFium draws an annotation that has
no `/Subtype` to name it by, or an empty one, the page's paths and images are
unknown.

An appearance is placed on the page as ISO 32000-1 (12.5.5) places it: its
`/BBox`, transformed by its `/Matrix`, is scaled and moved onto the
annotation's `/Rect`, and each point goes through the `/Matrix` and then that
placement. A `/Rect` or `/BBox` that is not four numbers, a `/Matrix` that is
not six, a box without area and a placement that is not finite leave the
annotation's facts unknown, `invalid-in-file`.

| Value | Where it is unknown |
| --- | --- |
| fill and stroke colour | where the content, or a form it draws, selects a pattern colour space or sets a pattern colour (PDFium then reports a colour of its own), where QPDF cannot read that content or warns that it misread an object of the file, and where PDFium gives no colour. QPDF's warnings gather as it reads the file, so a colour known at first can be unknown once another page was read |
| line width, dash | where the transform does not scale every direction alike: its two scales differ by more than a thousandth, or the cosine of the angle between its two directions exceeds about a thousandth; elsewhere they are given in user space, scaled by the geometric mean of the two scales. A negative width or dash length and a dash of zeros are `invalid-in-file`; a transform that makes every dash length 0 leaves the dash unknown |
| points | where a point is not finite in user space, `invalid-in-file` |
| cap, join, draw mode, points, matrix | where PDFium gives none |
| an image's values | where PDFium gives no metadata, all of them but its corners; its colour space and bits per pixel where PDFium cannot decode it (it reports 0 bits per pixel); a pattern colour space is `invalid-in-file`; a pixel size outside 1 to 2^31 - 1 is `invalid-in-file` |
| everything of the page | where a form in the page's content lies deeper than PDFium reads forms, 40 levels |
| everything of an annotation | where a form in its appearance lies deeper than 40 levels |

The page's paths and images are unknown as a whole, with that reason, where
QPDF's values are all unknown, where the engines see the page differently
(its object number is unknown), where `/Annots` is not an array, and where
the engines count different annotations: without QPDF, an appearance cannot
be checked and a colour not judged ("Object identity" names this exception).

The entries of a page's `/Annots` are held to `max_items_per_page`, and so
are its paths and images together; above it the page is refused with
`LimitExceededError` before more is read. Where the paths and images exceed
it, reading stops at the first one beyond, and `found` is the limit plus one.

Not applied: clip paths and optional content; a path is given as the content
draws it. Not given: shadings painted with `sh`, the cells of patterns, the
glyphs of Type 3 fonts, text.

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
does not expose object numbers. The mapping held for all 7934 widgets of the
104 corpus files (7935 on the 109 files of the first measurement, five of
them test files).

The corpus contains no damaged files, and on a damaged file the two engines may
recover different page trees or annotation lists. Therefore the mapping is
checked at run time:

| When | Check |
| --- | --- |
| on open | both engines report the same number of pages |
| on open | every object stream the cross-reference names can be read, and its header names every object once, never object 0, at the index the cross-reference gives. QPDF keeps the last of two objects with one number and finds an object by the number in the header; PDFium takes what sits at the index. Where this does not hold, every value from QPDF is unknown for the document |
| before a value from QPDF is used for a page or an annotation | the object at the same position is the same in both engines. For a page, PDFium not exposing object numbers, that means the same visible box, within 0.001, and the same rotation. QPDF's visible box is its media box, US letter where it has none it can use, intersected with its crop box, corners in order; its rotation is taken modulo 360, and a value that is not a multiple of 90 counts as 0. For an annotation: PDFium reads a widget at the same index, and the widget's own dictionary agrees: rectangle, flags, /T, /AS, /Parent, /V, /DV, /TU, /FT; the page's annotation counts are equal |

When a check fails, the values that depend on it are unknown and the reason is
stated. Facts read through PDFium are returned as usual, except paths and
images, whose appearances cannot be checked without QPDF ("Paths and images").

No PDFium call gives an object number, so the check on open is made on
QPDF's side alone. Measured: all 104 corpus files pass it; 93 of them use
object streams.

A known gap (measured): the cross-reference itself can make the engines
resolve one object number to different objects. An object listed twice in
one section is read by QPDF from the first entry and by PDFium from the
last; an `/XRefStm` in the oldest section is read by QPDF only. The check
on open does not see this. Where it happens to a page, QPDF's values of the
page can describe another object than PDFium's; no corpus file does it.
`recover()` writes such a file, and a file the check on open refuses, as
QPDF reads it, without a note.

Reason: a wrong object number is worse than none. A later change to the file
would address the wrong object.

## Forms

### Text strings and names

A text string of the file, such as a field's name or label, is decoded as
ISO 32000-2 7.9.2.2 says: UTF-16BE after the bytes FE FF, UTF-8 after
EF BB BF, PDFDocEncoding otherwise. UTF-16LE after FF FE is read as well,
as both engines read it. Language escape sequences (U+001B, a language
code, an optional country code, U+001B) are removed from the Unicode
forms; an escape that is not closed is removed with all that follows it.
What does not decode becomes U+FFFD: an odd last byte, a lone surrogate,
invalid UTF-8, and the bytes 0x7F, 0x9F and 0xAD, which PDFDocEncoding
leaves undefined. Decoding never fails.

Reason: PDFium gives U+0000 for the undefined bytes, keeps lone
surrogates and drops invalid UTF-8; pikepdf gives U+FFFD for the
undefined bytes and fails on invalid UTF-8 (measured). U+FFFD marks what
the file holds without making up a character.

A name, such as an appearance state, is returned as its exact bytes and
as text: UTF-8 where the bytes are valid UTF-8, Latin-1 otherwise, with
the encoding stated. Reason: 59 appearance states in the corpus are
Latin-1 bytes and no valid UTF-8; PDFium decodes every name as a text
string, which garbles UTF-8; filling a form writes the exact bytes back.

### Fields and widgets

`pdfacts.widgets(document, page_index)` returns the widgets of one page,
`pdfacts.fields(document)` the terminal fields of the document, depth first
from `/AcroForm /Fields`. Every value comes from QPDF: the file content, read
through its raw objects. PDFium is asked one thing per widget: whether it
reads a widget at the same index of the page's `/Annots` whose own
dictionary agrees — rectangle, annotation flags, `/T`, `/AS`, `/Parent`,
`/V`, `/DV`, `/TU`, `/FT`, and the page's annotation counts are equal. Only
the widget's own dictionary is compared: values inherited from a parent
field, `/Opt` and the appearance streams are not. Where
the check fails, every fact of the widget is unknown with the reason
`engines-disagree`, and the detail names the attribute that differs; where a
widget appears on one side only, the same unknown says so. Where neither the
rectangle nor the own `/T` could be compared, nothing identifies the widget
and the check fails. PDFium holds rectangles as 32-bit floats, so corners are
compared within 0.001 plus a millionth of the larger magnitude, and its flag
field is signed, so an integer `/F` is compared as written, modulo 2^32, and
an absent one as 0. Texts are compared after
deleting U+0000, U+FFFD and lone surrogates, `/AS` only for names of at most
85 bytes (PDFium keeps 255 bytes of a name's syntax). The cross-reference
itself is not checked; the object-stream check of `open()` secures what
PDFium cannot see, and the cross-reference remains a known gap (see "Object
identity").

The terminal field of a widget is its own dictionary when it has `/T` or no
`/Parent`, else its `/Parent`. Inheritance of `/FT`, `/Ff`, `/V`, `/DV` and
`/Opt` (for buttons) starts at the terminal field; `/Opt` of a choice field
is not inherited. A widget's field is unknown as a whole when its `/Parent`
chain loops or holds something that is not a dictionary, when the chain is
longer than 33 dictionaries, when the terminal field's node in the field tree
was not followed (the tree is deeper than 32 levels there), or when the link
does not hold: the parent's `/Kids` must list the widget once, every
entry of the `/Kids` must be a widget dictionary, the widget must not itself
be a field node of the tree, and the terminal field must be a terminal node
there. Names are the non-empty
partial names joined with `.`; a partial name holding `.` makes the name
unknown. Unknown field flags make the type unknown. A pushbutton has no
value; the value of a signature field is not read. Where the field tree node
is structurally invalid, every fact of the field but its reference and
`in_form` is unknown. `in_form` is true when the terminal field is a node of
the tree, whatever else the tree holds; false when there is no `/AcroForm`;
unknown when the tree is invalid or cut, or the terminal field has no object
number. A `Field` returned by `fields()` equals the `field` of every widget
of it whose check passed.

Entries of a page's `/Annots` are counted before any is read, against the
`max_items_per_page` limit; the deadline is checked when `widgets()` or
`fields()` begins.

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

A recovery can lose pages, or objects a page uses, and still succeed, and the
library cannot tell: the engine that rebuilds the file has repaired its page
tree before anything can be read, so the number of pages the damaged file was
meant to have is not known. The result states its own page count; a caller
who knows how many pages to expect compares the two. Whether a partly
recovered file is acceptable is the caller's decision.

A known gap (measured): QPDF reads a file whose trailer it cannot find as
unencrypted, since the trailer names the encryption dictionary. Where the
dictionary is left, recovery fails; where the cut took it too, nothing tells
that the file was encrypted, and the result is an unencrypted file whose
strings and streams are still encrypted. Numbers are not encrypted, so its
page geometry holds. Of the 14 encrypted forms in the corpus, 13 keep the
dictionary at the start of the file.

These count as files the engine cannot load:

| Case | Reason |
| --- | --- |
| the file does not end with `%%EOF` and white space: there is no `%%EOF`, or anything but white space follows the last one | a file cut short can end in an earlier revision's cross-reference table, which the engines then read without a word; so can a file with bytes appended |
| PDFium can read the file only by rebuilding its cross-reference table | that is a repair |
| PDFium finds no page, or no page dictionary for one of the pages it counts | an empty page tree, a missing one and one that names no object look the same to PDFium; the count comes from `/Count` and can promise pages that are not there |
| part of a revision, an object, a cross-reference section or a trailer, follows the end of the last whole revision where QPDF reads the file to: the last `startxref` cannot be read, or the last update is unfinished. A comment that holds what looks like one of them counts too | the engines read the revision before without a word (#32) |
| after the end of the last whole revision, the last `startxref` whose offset can be read names another section than that revision's, one in a comment too: an earlier revision's table, another table, or an object | the engines follow it there instead of to the last revision's section, and where that is an earlier revision's table they read that revision without a word (#44) |

A known gap (measured): the test does not know comments, and it finds the
first-page trailer of a linearized file only where at most one object lies
between the linearization dictionary and the first-page cross-reference
section. So a comment holding what looks like an object, a cross-reference
section or a trailer after the last revision, and a linearized file with two
or more objects in that place, count as damaged. No corpus file has either,
and QPDF writes neither. A `startxref` in a comment counts, since both
engines read one. In crafted layouts the engines disagree and the test
passes the file: PDFium also reads an offset followed by a point, a plus or
a minus sign, such as `392.0`, and one that names more than 64 bytes of
white space before a table; and where a `startxref` stands in the comment
between another `startxref` and its offset, the engines read different
ones. There `open()` warns.

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
dictionary for every page. None of them has part of a revision, or a
`startxref`, after its last whole revision. A linearized corpus form cut at
60 % opened in PDFium, which rebuilt its table. PDFium looks for `startxref` in
the last 4096 bytes, so more than about 4 KB of white space after `%%EOF` makes
it rebuild the table, and the file counts as damaged.

## Limits

The library checks what it can check between two engine calls:

| Limit | Checked |
| --- | --- |
| size in bytes | before opening |
| number of pages, number of objects | after opening, before any page is read |
| number of items per page | while facts are collected |
| deadline | between pages |
| pixels of one rendering | before the engine is called |
| content streams PDFium parses for one page | before PDFium loads the page, for its geometry, a rendering or its annotations |

Time and memory cannot be enforced from inside a running engine call. They are
the caller's responsibility; a separate process per document is the
recommended way.

Loading a page is one such call: PDFium parses the page's content and every
form it draws, down to 40 levels. A page of a few kilobytes whose forms each
draw the next twice makes that call take about 1 GB at 18 levels and 3 GB at
20, and a form that draws itself twice makes it not return (measured).
`max_forms_per_page` is checked before PDFium loads a page. QPDF counts the
content streams PDFium would parse for the page, rendered with its
annotations:

- each form each time it is drawn, down to 40 levels below the content that
  draws it; one drawn deeper counts once and is not parsed;
- a tiling pattern and a soft mask group once, when first used, and the glyph
  procedures of a Type 3 font once, all of them, when the font is first
  selected, or, for a font without `/Resources`, once for each place it is
  selected;
- for each annotation, each stream of its normal appearance, hidden
  annotations too;
- a page's content and an appearance each start their own 40 levels; a
  pattern, a soft mask group and a glyph procedure are counted as if they did
  too, which can only count more.

Names are looked up as PDFium looks them up: in a stream's own `/Resources`;
without them, a form uses those of what draws it, an appearance and a soft
mask group the page's, a pattern none; where the resources in use lack the
kind of name (`/XObject`, `/Font`, ...), PDFium looks in the page's, except for
a pattern. Where PDFium reads leniently, so does the count (measured,
2026-10-01): a stream's dictionary wherever a dictionary is expected, and a
`/Subtype` written as a string as well as a name. PDFium was measured to take
a stream's dictionary at `/Resources`, at a group of names and at `/AP`, not
at an `/ExtGState` or a font; taking it everywhere counts the same or more,
never less. A page's resources are inherited through the page tree, and the
parts of `/Contents` are read as one stream. The name an operator uses is
taken next to it, as PDFium takes it. Where PDFium parses less, the count is an
upper bound: it parses only the glyphs it shows and the one appearance it
selects.

Where QPDF cannot count, because it cannot open the file, the check of object
streams failed, the engines count different pages, the pages QPDF found are
not the file's, it cannot read one of the content streams, its parser warns
that it mis-read an object — any warning that names an object and its offset;
a name with a `#00`, an integer beyond 64 bits, a wrong or missing `/Length`,
a broken hex string (measured, 2026-10-01) — or the two engines read an
object differently, the entry of an object stream whose body is a stream
object, which QPDF reads as a dictionary and PDFium parses as a stream
(measured, 2026-10-01), a set limit makes `open()` or the page fail with
`LimitUncheckedError`. The warnings that name no object and no offset stay
countable — the page-tree repairs of the walked view, the recoveries of a
content stream — and the corpus gives none of the refusing ones at all. The count reads
every entry of the page's `/Annots`, also where `max_items_per_page` would
refuse the page's annotations. It may add QPDF's warnings about content
streams to the document's warnings, and pikepdf warns of content that ends
inside a string; where the caller makes warnings errors, such a page cannot be
counted. Counting takes memory with the size of the page's content, up to
about twice what loading the page takes (measured: 1.2 GB against 0.6 GB for a
`/Contents` listing one stream 300 times), and time with that size and the
places a stream without `/Resources` is drawn; the deadline is checked after
it. A known gap: a page is counted
before the engines' views of it are compared, and where the two engines read
different objects (see "Object identity") the count can miss what PDFium
parses.

The numbers are passed in by the caller. The library has no built-in defaults.

For annotations, the number of items is the length of the page's `/Annots`,
checked before any entry is read; the deadline is also checked when
`widgets()` or `fields()` begins.

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

Exact values are hashed, and platforms are compared by hash. A float is
written as the shortest text that reads back to the same number, negative zero
as zero; nothing is rounded. `bench/digest.py` states the encoding.

| Measurement | Result |
| --- | --- |
| PDFium, unrounded values, Linux arm64 against Linux x86_64 | glyph, path and widget digests identical for all 104 forms |
| PDFium, unrounded glyph values and page geometry, host fonts ignored, macOS arm64 against Linux arm64 and Linux x86_64 | identical for all 104 forms (M1 gate, 2026-09-29) |
| PDFium, values rounded to three decimals, host fonts ignored, macOS against Linux | identical for all 104 forms |
| the reference engine, word coordinates rounded to three decimals, macOS against Linux | identical for 83 of 104 forms |

The last row shows that rounding does not make nearly equal values hash
equally: two values on either side of a rounding boundary still differ.

The second row is the measurement of the M1 gate. This section had laid down
beforehand what it would decide: unrounded digests identical on all three
platforms mean that exact values are hashed; had they differed, platforms would
be compared fact by fact with a tolerance, not by hash.

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

`glyphs(document, page_index)`, `words(document, page_index)` and
`lines(document, page_index)` give them, each a tuple. Where a form lies
deeper in the page's content than PDFium reads forms, 40 levels, the page's
text is unknown as a whole, `unreadable`: PDFium's text page leaves out what
such a form draws without a sign. The characters PDFium reads from the
content count against `max_items_per_page`, white space among them; above
the limit the page is refused, and no more characters are read than the
limit and one, though PDFium has made the whole text page by then. PDFium loads the
page only once `max_forms_per_page` is checked; apart from that count, which
QPDF makes, nothing of the text depends on QPDF: where QPDF's values are
unknown, the text is given as usual ("Object identity").

PDFium leaves out of its text page what it takes for text drawn again, as a
bold face drawn twice is, and keeps the first drawing, its render mode with
it. What is not on its text page is no glyph. Measured at size 10, the font
policy installed, in a font 1000 thousandths wide unless named:

| Drawn again | Left out | Kept |
| --- | --- | --- |
| a text object with the codes of one of the five text objects before it | "AB" 7.5 to the right; "ABCDE" 8.9 to the right or 5.5 up; Helvetica's "i" 0.6 to the right; at one place, with four text objects between, or eight paths | "AB" 8 to the right; "ABCDE" 9.5 to the right or 6 up; Helvetica's "i" 0.8 to the right; five text objects between |
| a character with the code and font of one of the six characters before it | the "A" of "AC" 0.6 to the right of "AB"; the "A" of "AX" at the place of "ABCDEF"; the "H" of "Hello" at the place of an "H" at size 12 | the "A" of "AC" 0.8 to the right, or in another font; the "A" of "AX" at the place of "ABCDEFG" |

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
reaching past it, so that the next step is one back; letters that `Tc` spaces
by 0.8 of the size or more each stand on a line of their own; so do words
that a `TJ` spaces 0.8 of the size or more apart, as justified text may, for
a `TJ` sets columns apart the same way; and a run of spaces, which PDFium
gives as one character at the first one's origin, leaves the width of the
others as a gap, and a gap of 0.8 of the size or more begins a new line. Text
drawn again where PDFium leaves it out is lost, though drawn on purpose: two
lines of the same ten characters 7 apart at size 10 come out as one; and text
drawn invisible under the same text drawn visibly gives that text render mode
3. Not given: words and lines of text that is not upright, and text in
annotation appearances.

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
| fill and stroke colour | where the content, or a form it draws, selects a pattern colour space or sets a pattern colour (PDFium then reports a colour of its own), where QPDF cannot read that content or warns that it misread an object of the file, where the pattern check reads a name without bytes from the content ("Text strings and names"; measured, 2026-10-03), and where PDFium gives no colour. QPDF's warnings gather as it reads the file, so a colour known at first can be unknown once another page was read |
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
| on open, before the object streams | both engines read the file as encrypted, with the same revision of the security handler (`/R`), or both as not. QPDF takes `/Encrypt` from the newest trailer only, PDFium from the newest trailer that writes it, so an update whose trailer leaves it out hides the encryption from QPDF, which reads the file as not encrypted, or cannot open it where it needs an object stream to find the pages, while PDFium decrypts it (measured). Where this does not hold, every value from QPDF is unknown for the document; the facts read through PDFium are given as usual, though PDFium may be the engine that reads the encryption wrongly, where the cross-reference gives it another encryption dictionary than QPDF (the known gap below; measured). The check compares `/R` only. Where the newest trailer writes `/Encrypt null` or a reference to no object, neither engine decrypts the file: both read the encrypted bytes, `open()` gives the facts of those bytes with nothing to say so, and `recover()` refuses such a file (measured) |
| on open | every object stream the cross-reference names can be read, and its header names every object once, never object 0, at the index the cross-reference gives. QPDF keeps the last of two objects with one number and finds an object by the number in the header; PDFium takes what sits at the index. Where this does not hold, every value from QPDF is unknown for the document |
| before a value from QPDF is used for a page or an annotation | the object at the same position is the same in both engines. For a page, PDFium not exposing object numbers, that means the same visible box, within 0.001, and the same rotation. QPDF's visible box is its media box, US letter where it has none it can use, intersected with its crop box, corners in order; its rotation is taken modulo 360, and a value that is not a multiple of 90 counts as 0. For an annotation: PDFium reads a widget at the same index, and the widget's own dictionary agrees: rectangle, flags, /T, /AS, /Parent, /V, /DV, /TU, /FT; the page's annotation counts are equal |

When a check fails, the values that depend on it are unknown and the reason is
stated. Facts read through PDFium are returned as usual, except paths and
images, whose appearances cannot be checked without QPDF ("Paths and images").

The inspection is an exception to the check before a value from QPDF is used
for a page or an annotation: it reads through QPDF alone, and gives the pages
and the page and annotation index of an entry as QPDF reads them, unchecked
("Inspection"; decision of the maintainer, 2026-10-04).

No PDFium call gives an object number, so the check of the object streams is
made on QPDF's side alone. Measured: all 104 corpus files pass it; 93 of them
use object streams. All 104 pass the check of the encryption too; 14 of them
are encrypted.

A known gap (measured): the cross-reference itself can make the engines
resolve one object number to different objects. An object listed twice in one
section is read by QPDF from the first entry and by PDFium from the last; an
`/XRefStm` in the oldest section is read by QPDF only. The check of the object
streams does not see this; those of the encryption and of the page count see
it only where the engines then read another revision of the security handler
(`/R`), or another number of pages. Where it happens to a page, QPDF's values
of the page can describe another object than PDFium's; no corpus file does it.
`recover()` writes such a file, and a file the check of the object streams
refuses, as QPDF reads it, without a note.

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

A name that writes a `#` not followed by two hexadecimal digits is not a name
ISO 32000-2 allows (7.3.5), and has no bytes: QPDF holds a NUL in place of the
`#`, and PDFium reads the two characters after it as hexadecimal digits, one
that is none as 0, and keeps a `#` among the name's last two characters
(measured). A value that is such a name is unknown, invalid in the file. Where
one of an annotation's appearance states is such a name, which appearance
PDFium draws is not known, since it may select that state, and the
annotation's graphics are unknown ("Paths and images"); where its `/Subtype`
is such a name and PDFium draws it, no graphics of the page are known, as
where it has none.

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
85 bytes (PDFium keeps 255 bytes of a name's syntax), and no name without bytes
("Text strings and names"). The cross-reference
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

## Inspection

`inspect(document)` reports what QPDF reads of the file's structure and its
active content: the trailer's `/Size`, the objects by kind, each page's
`/Contents` with the `/Length` each of its streams writes, the encryption, and
the actions, the XFA and the embedded files. Every value is read through QPDF,
with its recovery off as `open()` reads ("Damaged files"); none through
PDFium.

The inspection reads every object of the file, those an object stream holds
included, from views of its own, opened for the call and closed after it, and
of the pages nothing but their objects, so that it changes no other answer of
the document. Its time and memory grow with what those objects hold as
decompressed. `max_bytes` bounds the file, not what its streams decompress to;
`max_objects` bounds the objects the cross-reference lists, not the direct
objects one of them holds; `max_items_per_page` does not apply. One object can
hold hundreds of thousands (measured on macOS arm64, in several runs: a file
of 7 kB whose one object, in an object stream, holds an array of 320,000
dictionaries took 5 to 16 s; one of 28 kB whose one object holds a million
actions so, 18 to 43 s, the process then holding 0.9 to 1.1 GB). The deadline
is checked when `inspect()` begins, and again before it reads every object a
second time for the active content; a separate process per document is the
guard, as for an engine call ("Limits"). The inspection has no limit of its
own (decision of the maintainer, 2026-10-03).

Where every value from QPDF is unknown ("Object identity"), every part of the
inspection is, with the same reason, and it has no warnings. Where QPDF
warned, reading every object, that it mis-read one, a warning that names an
object and its offset ("Limits"), the object counts, the pages and the active
content are unknown, unreadable: QPDF reads such an object as null and drops a
key whose value it reads as null, so a page's `/Contents` or an action can
look absent. The encryption is given where QPDF found it, but an entry of it
that QPDF finds absent is then unknown, unreadable, for it may be one QPDF
dropped; so are the crypt filters of `/CF`, and a method that rests on an
entry or a crypt filter QPDF does not find. Whether the file is encrypted at
all is checked against PDFium when it is opened: QPDF drops the trailer's
`/Encrypt` where it reads the dictionary as null, and PDFium may decrypt the
file all the same; the engines then disagree, and every value from QPDF is
unknown (measured). Where the pages QPDF found are not the objects the file's
page tree lists, the pages are unknown, unreadable, and so are the actions and
the entries that name them, which are listed in the order of the pages. A
page, and the page and annotation index of an entry, are as QPDF reads the
page tree and each page's `/Annots`: the inspection reads through QPDF alone,
and checks none of them against PDFium as a value from QPDF is checked before
it is used for a page or an annotation ("Object identity", which names this
exception). The warnings QPDF gave while it read are given each once, in the
order it gave them.

A page names what its `/Contents` names by an index: a stream or an indirect
array that several pages name is listed once, and a stream that an array names
many times is read once, so that what the file shares is not multiplied
(measured: 100 pages naming one array of 20,000 entries, a file of 2 kB, took
6.2 s listed for each page, 0.2 s listed once). A content stream's `/Length`
is given as written, an integer that leads to `endstream` (QPDF reads no
other); one below 0, which QPDF reads as 0, warning of no mis-read object, is
unknown, invalid (measured); in an encrypted file it is the length of the
encrypted data, which with AES holds 16 bytes of initialization vector and 1
to 16 bytes of padding (81 of the corpus's 948 content streams).

The encryption is the dictionary's entries as written, named by its object
(none where the trailer holds it directly), with the crypt filters `/CF`
defines, in the order of their names' bytes, and the `/CFM` of each, and the
length of QPDF's key. An entry of another type than ISO 32000-2 gives it (a
name, `/CF` and each crypt filter a dictionary, a stream not counted as one,
`/Length` an integer, `/EncryptMetadata` a boolean) is unknown, invalid:
PDFium reads `/StmF`, `/StrF` or `/CFM` written as a string as the name, QPDF
as absent, and `/CF` written as a stream as its dictionary, QPDF as none
(measured). `/V`, `/R` and `/P` are integers, and `/V` is 1, 2, 4 or 5: QPDF
opens no file otherwise (measured). The permissions are the bits of `/P` as
ISO 32000-2, Table 22, names them, True where the bit is 1; revision 2 defines
bits 3 to 6 only, and the others are None there; bit 10, extraction for
accessibility in ISO 32000-1, is one ISO 32000-2 no longer uses and has
writers set. They are reported, not enforced. The method of a handler of `/V`
1 or 2 is RC4, which it uses without crypt filters (Table 20). Of `/V` 4 or 5
it is that of the crypt filter `/StmF`, `/StrF` or `/EFF` names as written:
identity where it names `/Identity` or is absent (an absent `/EFF` names what
`/StmF` names); otherwise as the `/CFM` of the crypt filter `/CF` defines by
that name gives it (Table 25): RC4 for `/V2`; AES for `/AESV2` and `/AESV3`,
with a key as long as `/V` makes it, for both engines decrypt either so
(measured); unknown, invalid, for another name, or where `/CF` defines no such
filter. `/CFM /None`, its default, leaves the decryption to the security
handler; the standard security handler defines none, and the engines take such
a filter differently (measured: PDFium decrypts it as RC4, so a file encrypted
with RC4 and not one encrypted with AES; QPDF names its method "none" or
"unknown", and decrypts neither, but one encrypted with AES whose `/CFM` is
`/None`), so the method is unknown, the engines disagreeing. So is it where
`/CF` defines a filter by the name `/Identity`: PDFium takes `/Identity` for
the identity filter, QPDF for that filter (measured). PDFium opens no file
whose `/StmF` and `/StrF` name different filters (it reads a string written
there as the name), name a filter other than `/Identity` that `/CF` lacks, or
are both absent, so `open()` refuses those. Neither engine heeds `/EFF`: each
decrypts an embedded file with the method of `/StmF` (measured), so where
`/EFF` names another filter, the engines read embedded files otherwise than
the method the file gives for them. Whether the user's and the owner's
password matched is of the password given to `open()`, no password being the
empty one; both, where the two are the same.

Active content is listed in full, with no verdict: which kinds matter is the
caller's to decide (decision of the maintainer, 2026-10-02). An action is
listed once, with its kind (`/S`, a name as "Text strings and names" gives
names; unknown, invalid, where it is no name) and the object it is written in:
its own, or the one that holds it. An entry that names an action to run is
listed once, with its place, the object its dictionary is written in and its
event, as ISO 32000-2, 12.6.3, names them: the catalog's `/OpenAction` and the
events WC, WS, DS, WP and DP of its `/AA`; the JavaScript name tree; the
events O and C of a page's `/AA`; an annotation's `/A`, but a Movie
annotation's, and the events E, X, D, U, Fo, Bl, PO, PC, PV and PI of its
`/AA`, with the annotation's page and index; the events K, F, V and C of a
field's `/AA`; an outline item's `/A`. A key of `/AA` that is no event of its
place runs nothing and is no entry.

The fields are the dictionaries of `/AcroForm`'s `/Fields` and of their
`/Kids`, those of its `/CO`, the widget annotations the pages list, and the
`/Parent` of each: PDFium takes a widget outside `/Fields` for a field once a
page is loaded, and MuPDF runs the C of each field of `/CO` (measured; read).
An entry is listed where it is written. PDFium reads a field's events from the
nearest `/AA` up its `/Parent`, MuPDF each event, an annotation's too, from
the nearest `/AA` that holds it (measured; read); the listing gives each `/AA`
where it is written and leaves that reading to the caller. The events of an
annotation written in a field that no page lists are listed with no page, as
MuPDF reads them for the field's widgets.

In the active content, a stream is read as its dictionary wherever a
dictionary is read. PDFium does so for the action of `/OpenAction` and of a
link's or an outline item's `/A`, and for a page's `/AA`; not for a `/Next`, a
value of the name tree or an annotation (measured). MuPDF reads every stream
as its dictionary (read).

An action that no entry names is listed too: every object is read, and a
dictionary whose `/S` names one of the action types of ISO 32000-2, Table 201,
or whose `/Type` is `/Action`, is an action wherever it is written (the
trailer of a cross-reference table, which is no object, aside). In the corpus,
48 of 1287 actions are such: 47 in objects no path from the trailer reaches,
one the `/A` of an annotation only the structure tree names. A name is
compared as its bytes.

Each action, each `/Next` and each `/EF` is listed once and named by its
index, and each entry once; a value written as an object of its own that many
dictionaries name, an action's `/S`, an annotation's `/Subtype`, a crypt
filter or its `/CFM`, is read once: what the file shares is not multiplied,
however a caller takes the values. The actions come in the order the entries
reach them, each followed by those its `/Next` names, depth first, then those
no entry reaches, in object order; the entries in the order: the catalog's,
the name tree's, each page's followed by its annotations', the fields', the
outline's.

The XFA is the streams of `/AcroForm`'s `/XFA`, a stream or those of an array;
unknown, invalid, where it is neither, or an array but not of pairs of a
packet's name, a string, and its stream (ISO 32000-2, Table 224). An embedded
file is a stream a file specification's `/EF` names; a stream typed
`/EmbeddedFile` that none names is not one (each of the corpus's 38 XFA
streams is so typed).

Not read: a page's presentation steps (`/PresSteps`, ISO 32000-2, 12.4.4.2),
whose actions are among those no entry names; the related files of a file
specification's `/RF`. Where the cross-reference makes the engines read one
object number as different objects ("Object identity", a known gap), the
inspection gives what QPDF reads.

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
| `NotEncryptedError` | `PdfactsError` | - | `decrypt()` was given a file both engines read as not encrypted |
| `DecryptionFailedError` | `PdfactsError` | `detail` | `decrypt()` refused the file, or its result failed a check |
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

## Decryption

`open()` reads an encrypted file as it is: the engines decrypt what they read.
Decryption is a separate, explicit step, like recovery ("Damaged files"):
`decrypt()` has QPDF write the file anew without encryption, checks the result
against the input, and returns the new bytes for the caller to open.

| Case | Result |
| --- | --- |
| the file is encrypted, and the result passes the checks | `Decryption`: the new bytes and their page count |
| both engines read the file as not encrypted, and nothing below says it may be encrypted | `NotEncryptedError` |
| `open()` refuses the file, or a page of it is damaged ("Damaged files") | the error of `open()`; `PasswordRequiredError` where a password is needed or wrong |
| the file is refused, or the result fails a check | `DecryptionFailedError`; never a file that did not pass |

Reason: decryption rewrites the file. The bytes and their hash change, and
facts read from the decrypted bytes describe the decrypted file, so the caller
has to hold those bytes.

Either password decrypts the file: the user password, which is empty where a
file sets only an owner password and then need not be given, or the owner
password. A known gap (measured): at revisions 2 to 4, QPDF does not take a
password outside ASCII as `open()` gives it, where PDFium does; every value
from QPDF is then unknown, and `decrypt()` refuses the file.

The permissions, the bits of `/P`, are not enforced: the inspection reports
them ("Inspection"), and the result has none, since it is not encrypted
(decision of the maintainer, 2026-10-02).

The input is opened as `open()` opens it, with its errors and its limits.
`decrypt()` then refuses a file in these cases, asked in this order:

| Case | Reason |
| --- | --- |
| every value from QPDF is unknown for the input, whether or not it is encrypted | the engines read its encryption otherwise, or QPDF does not read the file as PDFium does ("Object identity"); what QPDF would write could not be checked |
| neither engine reads the file as encrypted, and QPDF finds an encryption dictionary that the catalog does not reach | a newest trailer that writes `/Encrypt null`, or a reference to no object, hides the encryption from both engines (measured); the strings and streams may be encrypted with nothing to decrypt them |
| neither engine reads the file as encrypted, and QPDF warns that it mis-read an object | the object may be the encryption dictionary: where its object header is broken, neither engine decrypts the file (measured) |
| a crypt filter that `/StmF`, `/StrF` or `/EFF` names is neither absent, nor `/Identity` where `/CF` defines no filter of that name, nor defined in `/CF` with the `/CFM` `/V2`, `/AESV2` or `/AESV3` | the engines decrypt the others otherwise, or may (decision of the maintainer, 2026-10-04): without `/CFM`, PDFium decrypts with RC4 where QPDF writes the data as it is and reports nothing; with a filter named `/Identity` in `/CF`, QPDF decrypts and PDFium reads the data as written; with `/CFM /None`, QPDF warns that it may decrypt improperly (measured). Both engines decrypt `/AESV2` and `/AESV3` whatever `/R` says (measured) |
| an object reached from the catalog or `/Info` is one the engines decrypt otherwise: a stream whose `/Filter` names `/Crypt` and whose `/DecodeParms` give a `/Name`; a stream `/Type /XRef`; a dictionary whose `/Contents` is a string, an array or a dictionary, that PDFium takes for a signature, its `/Type` or, without `/Type`, its `/FT` being the name `/Sig` or a string that decrypts to `Sig`, and that QPDF does not, which takes only the name `/Type /Sig` with `/ByteRange`; under `/EncryptMetadata false`, the catalog's `/Metadata`, a string, an array, a dictionary or a stream, unless `/V` is 4 or more and it is a stream `/Type /Metadata` with `/Subtype /XML`; a string in an object the trailer holds directly, a direct `/Info` for one | QPDF takes the crypt filter `/DecodeParms` name, `/Identity` leaving the stream as written, where PDFium does not; QPDF leaves a stream `/Type /XRef` as written, PDFium decrypts it, and a cross-reference stream of the file is found through `startxref`, not referred to; PDFium leaves the `/Contents` of such a dictionary as written, QPDF decrypts it (measured for a string; no function of PDFium reads an array or a dictionary there, and they are refused alike); PDFium leaves the catalog's `/Metadata` as written whatever it is, QPDF only a stream of that type, and below `/V` 4 none, as it ignores `/EncryptMetadata` there; a string the trailer holds directly has no object number to decrypt it by, and QPDF leaves it as written where PDFium decrypts it (measured; decision of the maintainer, 2026-10-04). A `/Crypt` filter without `/Name`, as a corpus file writes on its metadata, both engines read alike (measured) |
| QPDF reports anything while it reads the file or writes it | it decrypts a stream only when it writes it, and says where it may decrypt improperly |

The checks (decisions of the maintainer, 2026-10-04): the result has to open
as `open()` opens a file, without a password; both engines have to read it as
not encrypted; its trailer has to have a usable `/Size`; and neither the
input nor the result may give a warning. For each page, the result has to give
what the input gives:

| Compared | Read by |
| --- | --- |
| the page's geometry: its visible box, rotation and user unit | both engines, as `open()` reads a page |
| for each entry of `/Annots`: whether it is a widget, its rectangle, its flags, `/T`, `/AS`, `/V`, `/DV`, `/TU` and `/FT` and their types, and the type of `/Parent` | PDFium |
| the characters of the page's content, as `glyphs()` reads them: each one's Unicode value, whether PDFium could not map it and whether it is a hyphen, its origin, matrix, font size and loose box, in content order, invisible text too | PDFium |
| the pixels of the page rendered in RGB, its annotations drawn as PDFium draws them by default | PDFium, at scale 1 where the page is at most 2^22 pixels there, else at the scale at which its width and its height in pixels, each plus one, multiply to 2^22: rounded up, a page of any shape is then at most 2^22 pixels |

A page of the result that is damaged where the input's is not fails the
checks. Object numbers are not compared: QPDF numbers the objects of the
result anew. Every page of the 14 encrypted corpus files renders the same,
pixel for pixel, before and after, and gives the same characters (measured,
2026-10-04).

Reason for the characters: where AES-encrypted data ends in inconsistent
padding, PDFium strips the last block by its last byte alone and QPDF strips
nothing (measured), so the end of a stream decrypts otherwise, and text in
invisible rendering mode changes where the rendering does not (decision of
the maintainer, 2026-10-04).

A known gap: the checks compare no other entry of an annotation, such as a
comment's `/Contents` or a link's `/URI`, and no string or stream that neither
the characters nor the rendering show, such as the metadata, an embedded
file, a script or an outline. Where the engines decrypt one of these
otherwise, as they do damaged AES data, inconsistent padding at its end for
one, the checks do not see it, nor a difference too small to change a pixel at
the scale rendered. What
the engines were found to decrypt otherwise is refused before QPDF writes,
read as QPDF reads the file: an encryption dictionary that one
cross-reference section lists twice, of which QPDF reads one and PDFium
another ("Object identity", a known gap), is not seen (measured).

The limits apply as for `open()`, to the input and to the result, with these
exceptions: `max_bytes` applies to the input only; `deadline_seconds` counts
from the start of `decrypt()`, checked as `open()` checks it, once the file is
written and after each page of the check. `max_items_per_page` holds the
entries of each page's `/Annots` that the checks read, as PDFium counts them,
and the page's characters, as `glyphs()` holds them; `max_render_pixels`
applies to the renderings of the check.

The result keeps the object streams the input has and gets none more; it is
not linearized, and its XMP metadata is left as it is. Its `/ID` keeps the
first part of the input's and gets a second made from its content, so the same
input gives the same bytes, whichever password was given. A signature in the
input no longer matches the bytes of the result.

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

The number of objects `max_objects` checks is the larger of the trailer's
`/Size` and the number of objects QPDF's cross-reference holds: QPDF reads any
of these whatever `/Size` says, with a warning where `/Size` is not one more
than the highest object number (measured).

A known gap (measured, 2026-10-03): for the check of object streams ("Object
identity"), `open()` decodes the object streams the cross-reference names,
each whole, before `max_objects` is checked; it stops at the first that fails
the check. That takes memory with the decoded size of the largest of them,
about three times that size, and time with their decoded sizes together: a 98
KB file whose one object stream decodes to 100 MB made `open()` take about 320
MB more at its peak. The objects of an object stream may be compressed, so
`max_bytes` does not bound this; a separate process per document is the guard,
as for the time and memory of an engine call (below).

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
streams failed, the engines read the encryption differently, the engines count
different pages, the pages QPDF found are not the file's, it cannot read one
of the content streams, its parser warns that it mis-read an object — any
warning that names an object and its offset; a name with a `#00`, an integer
beyond 64 bits, a wrong or missing `/Length`, a broken hex string (measured,
2026-10-01) — a name without bytes ("Text strings and names") that the name
operand of `Do`, `gs`, `scn`, `SCN` or `Tf` of a content stream writes,
refused as a mis-read object is: QPDF's warning about it names the content
stream and an offset, not in the form that names an object, and where
`/Contents` is an array no warning reaches the document (measured, 2026-10-03)
— or the two engines read an object differently, the entry of an object stream
whose body is a stream object, which QPDF reads as a dictionary and PDFium
parses as a stream (measured, 2026-10-01), a set limit makes `open()` or the
page fail with `LimitUncheckedError`. QPDF warns of an object it mis-read only
when it reads it, and its warnings gather as it reads the file, so a page that
could be counted at first can be refused once another page was read, as a
colour can become unknown ("Paths and images"). Reading every object when the
file is opened would end that, but it costs time and memory with the size of
every object once decoded, many times that size (measured, 2026-10-02: a 4 MB
array takes about 270 MB more), and the objects of an object stream may be
compressed, so that `max_bytes` does not bound it; so QPDF reads what the
pages read. The warnings that do not name an object and its offset in that
form stay countable — the page-tree repairs of the walked view, the recoveries
of a content stream — and the corpus gives none of the refusing ones at all; a
name without bytes is refused for itself, not for its warning. The count reads
every entry of the page's `/Annots`, also where `max_items_per_page` would
refuse the page's annotations. It may add QPDF's warnings about content
streams to the document's warnings, and pikepdf warns of content that ends
inside a string; where the caller makes warnings errors, such a page cannot be
counted. Counting takes memory with the size of the page's content, up to
about twice what loading the page takes (measured: 1.2 GB against 0.6 GB for a
`/Contents` listing one stream 300 times), and time with that size and the
places a stream without `/Resources` is drawn; the deadline is checked after
it. A known gap: a page is counted before the engines' views of it are
compared, and where the two engines read different objects (see "Object
identity") the count can miss what PDFium parses.

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

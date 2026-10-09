# Changelog

Every change to the public interface or to a convention is recorded here.
Versions are 0.x until the first consumer has switched; until then a change
may break callers.

## Unreleased

### Added

- `TextRules`, with `V1` (M4a's rules) and `V2`, and `rules` of `glyphs()`:
  `glyphs(document, page_index, rules=TextRules.V2)` gives the codes the
  content shows, read on QPDF's tokens, white space included. `V1` stays the
  default.
- `rules` of `words()` and `lines()`: with `TextRules.V2`, the words and
  lines of those glyphs, a line's box holding the white space on the line,
  a word's text keeping U+0002 as it is.
- `annotations(document, page_index)`: the annotations of a page other than
  widgets, with their `/Rect`, `/F`, `/Contents`, `/C` colour and the
  appearance their `/AP` selects, as the convention "Annotations" states.
  The types `Annotation` gains `contents`, `colour` and `appearance`
  (fields with defaults; `Widget` unchanged in what it fills), the new
  `AnnotationAppearance`, and the capability `ANNOTATIONS`. `DeviceColour`
  moved from `pdfacts.forms.model` to `pdfacts.core.geometry`; the name
  `pdfacts.DeviceColour` and `pdfacts.forms.model.DeviceColour` are as
  before.

### Changed

- The order of milestones: M4b, the text probe and text in full, comes
  before the switch in M6 and before version 1.0, no longer after them. The
  switch waits in the first consumer's project, and what the probe decides
  about text can still change the interface without a major version.
  Decision of the maintainer, 2026-10-06.

### Conventions

- "Glyphs, words and lines": the glyphs of `TextRules.V2`, their values, and
  where the text of a page is unknown with them; its words and lines, and
  that a page is read once for one rule set. "Word and line boxes": the line
  boxes of `TextRules.V2`. "Glyph boxes", "Text" and "Fonts" say what
  differs for them. Decisions of the maintainer, 2026-10-06 and 2026-10-07.
- "Glyphs, words and lines": the glyph boxes of `TextRules.V2` for fonts whose
  ascent and descent come from a font descriptor, Type 1, MMType1 and TrueType
  fonts and the descendants of Type 0 fonts, changed: a `/Descent` above 0 is
  read as its negation, an `/Ascent` at or below 0 is the top of the
  descriptor's `/FontBBox`, and where the box has no top above 0 the box is
  unknown, `invalid-in-file`. Decision of the maintainer, 2026-10-07 (#121).
- `THIRD_PARTY.md`: data extracted from Adobe's Core 14 AFM files and the
  Adobe Glyph List is shipped in `backends/adobe_fonts.py`, with their
  notices (decision of the maintainer, 2026-10-07).
- `docs/architecture.md`, "The two engines": pdfacts interprets content
  streams itself, on QPDF's tokens, where a fact needs what PDFium's
  interface does not give: the colour state that confirms a pattern colour,
  and from M4b the text of a second rule set, `TextRules.V2`. Its glyphs are
  checked against PDFium's characters on the corpus at the gate of M4b, not
  at run time. The text probe of M4b decided this; decisions of the
  maintainer, 2026-10-07.

## 0.2.0 (2026-10-06)

The interface as the review before 1.0 left it (A1 to A3): rendering
changes no fact, names and types made plain, the errors of each entry, rule
7 and the reason `not-read`. The first consumer has not switched yet.

### Added

- `UnknownReason.NOT_READ`, `"not-read"`: pdfacts does not read values of
  this kind; a later version may.

### Changed

These break callers, as 0.x allows; the review of the interface before 1.0
found them.

- `Bitmap.region` is now `Bitmap.display_region`: it is in display space,
  where `Page.render(region=)` takes user space, and the old name read as if
  the one could be given back as the other.
- `EngineInfo.font_policy` is a `FontPolicy`, or None until the runtime has
  started, where it was a string, `"not-started"` before the start.
- `FontPolicy.HOST` and `Capability.FONT_POLICY_HOST` are gone: the policy
  was never implemented. `configure(font_policy="host")` now raises
  `ValueError`, as any other name that is no policy does; it raised
  `UnsupportedError`.
- The value and the default value of a signature field, and those of a text
  field written as a stream, are unknown `not-read`; they were `unreadable`,
  which says that a value could not be read from the file.

### Conventions

- "Rendering" states that rendering changes no fact: pages are drawn through
  a document of their own, opened from the same bytes and password when a
  page is first rendered.
- "Paths and images" no longer says that an annotation PDFium generated an
  appearance for is unknown once the page was rendered: such an appearance
  is drawn only by the document that renders, from which no fact is read
  (consent of the maintainer, 2026-10-06).
- `docs/architecture.md`, rule 7: the version of the package is the version
  of the facts' structures, which carry none of their own; a minor version
  may add fields to a fact and members to an enum; a caller that builds a
  fact itself is not covered; a name that is to go is deprecated for at
  least one minor version first. "Three answers" has a fourth reason,
  `not-read`, and "Forms" uses it. "Glyphs, words and lines": `words()` and
  `lines()` keep their rules, and other rules come through a keyword or a
  function of their own. Decisions of the maintainer, 2026-10-06.
- "Errors" lists what each entry raises, measured, and no longer says that
  every error is a `PdfactsError`: a wrong argument raises `TypeError` or
  `ValueError`, and a page index outside the document `IndexError`, as they
  did before. It states that an engine's other exceptions are raised as
  `EngineError`, and that QPDF's messages to pikepdf's logger are not
  warnings of the document.

### Corrected

- Rendering a page could change the facts read after it. Drawing an
  annotation of some kinds that has no appearance (a square, a circle, ink,
  a highlight, an underline, a squiggly or struck-out line, a text note, free
  text), PDFium adds one to it, and `paths()` and `images()` gave it from
  then on as a path and an image whose values were unknown. Rendering now
  draws through a document of its own (#111).
- "Errors" said `ConfigurationError` is raised where the runtime is
  configured twice with different values. Configured before the start, it
  takes the last value; only another policy than the active one, after the
  start, raises it.

## 0.1.0 (2026-10-06)

The release the first consumer switches on.

### Added

- Error types: `PdfactsError` and its subclasses, listed under "Errors" in
  `docs/conventions.md`.
- `Unknown` and `UnknownReason` for values that could not be determined.
- The capability table: `Capability`, `capabilities()`, `require()`. Each
  capability becomes available with the change that implements it.
- Geometry: `Point`, `Rect`, `Matrix` and `PageGeometry`, with the transforms
  between user space and display space listed under "Coordinate spaces" in
  `docs/conventions.md`.
- The runtime: `configure(font_policy=...)` and `FontPolicy`. The policy
  "embedded-only" ignores fonts installed on the host; "host" exists and is not
  supported. The capability `FONT_POLICY_EMBEDDED_ONLY` is available.
- `open(data, *, password=None, limits=None)` opens a PDF from its bytes and
  returns a `Document` with `page_count`, `close()`, `closed` and a context
  manager. Before an engine sees the input: `EmptyInputError` for no bytes,
  `LimitExceededError` for more than `max_bytes`, `NotPdfError` without a
  header, `DamagedFileError` when the file does not end with `%%EOF` and
  white space, or when part of a revision, or a `startxref` that names
  another section, follows the end of its last whole revision. From PDFium:
  `PasswordRequiredError`, `DamagedFileError` (also
  when it has to rebuild the cross-reference table, finds no page, or finds
  no page dictionary for a page it counts), `EngineError`, and
  `LimitExceededError` for more than `max_pages`. Data that is not `bytes`, a
  password that is not a `str` and limits that are not a `Limits` raise
  `TypeError`; a password with NUL or one that cannot be written as UTF-8
  raises `ValueError`. Nothing is repaired.
- `Limits` with `max_bytes`, `max_pages`, `max_objects`, `deadline_seconds`
  and `max_render_pixels`; none is set by default. `max_objects` raises
  `LimitUncheckedError` when the object count is unknown; the deadline counts
  from the start of `open()` and is checked before each page is read.
- Pages: `Document.pages` (length, an index from 0, iteration; no slices, no
  negative index), `Page` with `index`, `geometry` and `ref`, and `PageRef`.
  Geometry comes from PDFium. Object numbers, `UserUnit` and
  `Document.object_count` come from QPDF, and are `Unknown` with the reason
  where QPDF cannot open or read the file or the engines disagree on the
  page count or on a page's visible box or rotation; a `UserUnit` that is
  not a number above 0 and a `/Size` that is not a whole number above 0 are
  `Unknown(INVALID)`. `UserUnit` is 1.0 where the file does not write it.
  `Document.warnings` gives the reasons first, then what QPDF reported; it
  grows as pages are read. The capabilities `PAGE_GEOMETRY`, `USER_UNIT` and
  `OBJECT_NUMBERS` are available.
- Rendering: `Page.render(scale=1.0, *, region=None, colour=Colour.GREY)`
  returns a `Bitmap`: the bytes (row 0 at the top), width and height as the
  engine made them, row length, channels, colour, scale, the region in display
  space and `display_to_pixel`. `region` is in user space and is cut to the
  page; a region without area on the page, or of which less than a pixel is
  left, raises `ValueError`, and so does a scale that is not finite and above
  0, or at which the page is more pixels than the engine addresses. A scale
  that is not a number and a region that is not a `Rect` raise `TypeError`.
  `Colour` is `GREY` or `RGB`, red, green, blue, given as a member or by its
  value. `Limits.max_render_pixels` is checked before the engine is called.
  The bitmap holds a copy of the bytes, and the engine's bitmap is freed. The
  capability `RENDERING` is available.
- Recovery: `recover(data, *, password=None, limits=None)` returns a
  `Recovery` with the recovered `data`, its `page_count` and the `warnings`
  QPDF gave while rebuilding and writing the file. The input is classified as
  `open()` classifies it, without the rule on the end of the file. QPDF reads
  the file up to the end of its last whole revision, leaving out a damaged
  end, and rebuilds a file without one from its objects. The result is
  written with `/Size` in the trailer, encryption kept, the content of the
  metadata kept, an unencrypted result the same bytes for the same input; it
  has to open as `open()` opens a file, without a warning and with `/Size`.
  `RecoveryFailedError` otherwise: when QPDF cannot rebuild the file; when
  the last revision is unfinished or its `startxref` leads nowhere, which
  QPDF would read as the earlier revision without a word or mix with it; and
  when an encrypted file has lost the trailer that names its encryption
  dictionary but kept the dictionary. `max_pages` and `max_objects` are
  checked on the result, `deadline_seconds` from the start of `recover()`. An
  intact file is rewritten, with no warnings unless QPDF reports a defect in
  it. The capability `RECOVERY` is available.
- A password given for a file that needs none no longer lets pikepdf warn:
  `open()` failed to open such a file with QPDF under a filter that turns
  warnings into errors.
- Fact types for forms: `Annotation`, `Widget`, `Field`, `FieldType`,
  `ChoiceOption`, `DeviceColour`, `BorderStyle`, the references
  `AnnotationRef` and `FieldRef`, and `Name` with `NameEncoding`.
- `widgets(document, page_index)` and `fields(document)`: the widgets of one
  page and the terminal fields of a document, every value from QPDF and each
  widget checked against what PDFium reads in its own dictionary.
- `Limits.max_items_per_page`: entries of a page's /Annots, checked before
  any is read.
- The capability `FORMS`.
- `Limits.max_forms_per_page`: the content streams PDFium parses for one
  page, counted by QPDF before PDFium loads the page; `LimitExceededError`
  above it, `LimitUncheckedError` where QPDF cannot count.
- Fact types for paths and images: `Path`, `Image`, `Subpath`, `Segment`,
  `SegmentKind`, `ShapeKind`, `FillRule`, `LineCap`, `LineJoin`,
  `DisplayColour`, `Dash` and `ColourSpace`; the geometry `Quad` and the
  reference `GraphicRef`, returned by `paths()` and `images()`.
- `paths(document, page_index)` and `images(document, page_index)`: what
  PDFium interprets for a page, its content and the normal appearance of each
  annotation, in drawing order, each appearance checked against the one QPDF
  selects and placed on the page as the file says; values that cannot be
  determined are unknown with the reason. The capability `GRAPHICS`.
- Fact types for text: `Glyph`, `Word`, `Line` and the reference `GlyphRef`;
  `glyphs()`, `words()` and `lines()` return them.
- `glyphs(document, page_index)`, `words(document, page_index)` and
  `lines(document, page_index)`: a page's text in content order, read
  through PDFium and made into glyphs, words and lines by the rules of
  "Glyphs, words and lines"; unknown as a whole where a form lies deeper
  than PDFium reads forms. The capability `TEXT`.
- `inspect(document)`, in the package `pdfacts.inspection`: what QPDF reads of
  the file's structure and its active content, every object read, as the
  convention "Inspection" states. The fact types `Inspection`, `ObjectCounts`,
  `PageContents`, `Contents`, `ContentsStream`, `ContentsForm`,
  `Encryption`, `CryptFilter`, `Permissions`, `CryptMethod`, `ActiveContent`,
  `Action`, `ActionTrigger`, `ActionPlace` and `EmbeddedFile`, and the
  reference `ObjectRef`. The capability `INSPECTION`.
- `decrypt(data, *, password=None, limits=None)`: an encrypted file written
  anew by QPDF without encryption and checked against the input, as the
  convention "Decryption" states. It returns `Decryption`; the errors
  `NotEncryptedError` and `DecryptionFailedError`. The capability
  `DECRYPTION`.
- `Pages`, the type of `Document.pages`, is exported.
- `pdfacts.__version__` is read from the installed distribution's metadata,
  as `engine_info().pdfacts` already was; both say 0.1.0 for this release,
  where `pyproject.toml` said 0.0.1 at every commit before.
- README, "Interface": the interface is `pdfacts.__all__`; facts are carried
  by `pickle`; the text of details, warnings and messages is not interface.

### Changed

- `EngineInfo` has a new required field `font_policy`: the active font policy,
  or `"not-started"`. Constructing an `EngineInfo` without it, for example
  from a receipt written by 0.0.1, raises `TypeError`.
- The minimum Python version is 3.13. It was 3.11.
- The order of milestones follows what the first consumer needs in order to
  switch: M1, M2 (forms), M3, M4a (text, first level), M5a (inspection, repair
  and decryption), then the switch in M6 for documents with form fields. The
  text probe and text in full (M4b), the edit session, annotations other than
  widgets, rotated text and the design note on form filling (M5b) come after
  the switch.
- The comparison with other libraries moved from M0 to M7.
- The division of work between the engines is described by kind of work:
  PDFium for what needs interpretation, QPDF for file structure.
- The repository uses no hosted continuous integration. The checks of every
  change run on the development machine: natively on macOS, and in containers
  for Linux arm64 and Linux x86_64.
- `max_objects` is compared with the larger of the trailer's `/Size` and the
  number of objects QPDF's cross-reference holds, which QPDF reads whatever
  `/Size` says: a file whose `/Size` is too small no longer passes.
  `Document.object_count` is still `/Size`.
- open() checks that the engines agree on the encryption: where PDFium reads
  the file as encrypted and QPDF as not, or the two read another revision of
  the security handler, every value from QPDF is unknown, engines-disagree, as
  where the page counts differ. An update whose trailer leaves out `/Encrypt`
  hid the encryption from QPDF, which read its values from the encrypted bytes
  where it could open the file. A limit `max_objects` or `max_forms_per_page`
  that is set now makes `open()` fail with `LimitUncheckedError` for such a
  file, as for a file whose page counts differ.

### Conventions

- The conventions of `docs/conventions.md` are binding since milestone M1 was
  done on 2026-09-29.
- A file counts as damaged also when it does not end with `%%EOF` and white
  space, when PDFium can read it only by rebuilding its cross-reference table,
  and when PDFium finds no page in it or no page dictionary for a page it
  counts.
- A file counts as damaged also when part of a revision, an object, a
  cross-reference section or a trailer, follows the end of its last whole
  revision: its last `startxref` cannot be read, or its last update is
  unfinished (#32).
- A file counts as damaged also when, after the end of its last whole
  revision, the last `startxref` whose offset can be read, one in a comment
  too, names another section than that revision's: the engines would follow
  it, and read an earlier revision without a word where it names one's table
  (#44).
- A page counts as damaged when it is read if PDFium's visible box for it is
  not finite or has no area, or the boxes the file writes leave no area (a
  flat crop box, or a media box that is flat or that, intersected with the
  crop box, has no area); the other pages are read as usual.
- The run-time check of a page compares its visible box, within 0.001, and its
  rotation. On QPDF's side the visible box is the media box, US letter where
  QPDF has none it can use, intersected with the crop box; the rotation is
  taken modulo 360, and a value that is not a multiple of 90 counts as 0.
- The font policy is installed before the first document is opened, and
  `engine_info()` names it in `font_policy`, because it changes the facts.
- The transform from user space to display space is the table under
  "Coordinate spaces"; display space is measured in user space units, and
  `UserUnit` does not scale it.
- Three answers: `None` for absent, `Unknown(reason)` for unknown, the
  capability table and an error for unsupported.
- Damaged files are recovered by an explicit step. `open` does not change what
  it reads.
- A recovery can lose pages, or objects a page uses, and still succeed; the
  library cannot tell, and the caller compares the page count with the one
  they expect. An encrypted file cut before the end of its encryption
  dictionary and its trailer is a known gap: its result is unencrypted, with
  its strings and streams still encrypted.
- The two engines are checked against each other at run time. A value that
  depends on a failed check is unknown.
- On open, every object stream the cross-reference names has to be
  readable, and its header has to name every object once, never object 0,
  at the index the cross-reference gives; otherwise every value from QPDF
  is unknown for the document, with the reason, and a set `max_objects`
  raises `LimitUncheckedError`.
- Limits: the library checks size, page count, object count, items per page
  and a deadline between pages. Time and memory are the caller's
  responsibility. There are no built-in numbers.
- Rendering returns raw bytes. No type of an imaging library appears in the
  public interface.
- A region to render is given in user space and cut to the displayed page.
  Pixel coordinates count from the top left corner; `display_to_pixel` is
  `Matrix(s, 0, 0, -s, -rx0·s, ry1·s)` for the region `(rx0, ry0, rx1, ry1)` in
  display space at scale s. The engine places points in 32-bit floating point:
  on pages of up to 2^20 pixels a side its own transform differs from it by
  up to about one pixel, and the edges it fills may snap to whole pixels; on
  larger pages the difference grows, to tens of pixels near 2^31 pixels a
  side. The pixels of one rendering are a limit, checked before the engine is
  called.
- There are two coordinate spaces, user and display. There is no third space
  with y pointing downwards.
- The digest is a tool under `bench/`, not part of the public interface.
- The digest hashes exact values, and platforms are compared by hash. The M1
  gate found the glyph data and page geometry of all 104 corpus files
  identical on macOS arm64, Linux arm64 and Linux x86_64 with unrounded values.
- Text strings are decoded as ISO 32000-2 says, UTF-16LE read as well,
  language escapes removed, and what does not decode given as U+FFFD. Names
  are returned as their bytes and as text, UTF-8 where valid, else Latin-1.
- The "Fields and widgets" section of "Forms": `widgets()` and `fields()`,
  every value from QPDF, each widget checked against what PDFium reads of its
  own dictionary, the terminal field with its inheritance and link rules, and
  the item limit and deadline of reading a page's annotations.
- A widget's facts are unknown unless PDFium reads a widget at the same
  index whose own dictionary agrees: rectangle, flags, /T, /AS, /Parent, /V,
  /DV, /TU, /FT, with the page's annotation counts equal.
- "Limits" gains a check before PDFium loads a page: the content streams it
  would parse, counted by QPDF, since a page of a few kilobytes can make that
  one engine call take gigabytes or not return.
- "Paths and images": facts in user space; where a subpath ends; the shape of
  each subpath (line, rectangle, quad, polyline, curves) by documented rules,
  numbers compared within 0.001 plus a millionth of the larger magnitude.
- "Limits": the count of what PDFium would parse follows its lenient reading
  (a stream's dictionary where a dictionary is expected, a string `/Subtype`),
  and a page where QPDF warns that it mis-read an object — any warning naming
  an object and its offset — or where the two engines read an object
  differently, a stream-object body inside an object stream among them, is
  refused with `LimitUncheckedError` where the limit is set, since a few
  kilobytes could otherwise make PDFium parse what QPDF counted as nothing.
- "Paths and images": where each value comes from, how an appearance is
  checked and placed, where colours, widths and image values are unknown,
  what is not applied or not given, and that paths and images count against
  `max_items_per_page`. "Object identity" names paths and images as the
  exception to facts read through PDFium being returned as usual: where QPDF
  cannot back the page (its values unknown, the engines seeing the page
  differently or counting different annotations, `/Annots` no array), the
  page's paths and images are unknown as a whole.
- "Glyph boxes": the advance box takes, along the writing direction, the
  extent of PDFium's loose box, since PDFium gives no advance width that can
  be relied on; across, the font's ascent and descent as before (decision of
  the maintainer, 2026-10-01).
- "Glyphs, words and lines": a page's text in content order; what a glyph
  is and its values; the rule that makes words and lines, a gap forward of
  0.15 of the glyph's size ending a word and a step of 0.8 either way
  beginning a line; what is passed over; what is known to go wrong.
- "Glyphs, words and lines": `glyphs()`, `words()` and `lines()`; a page's
  text unknown where a form lies deeper than PDFium reads forms; the
  characters count against `max_items_per_page`; the text drawn again that
  PDFium leaves out of its text page, the first drawing kept.
- "Glyphs, words and lines", what is known to go wrong: words a `TJ` spaces
  0.8 of the size or more apart begin new lines, and so does the glyph after
  a run of spaces that PDFium gives as one character, where the gap the
  others leave is 0.8 of the size or more (decision of the maintainer,
  2026-10-02).
- "Limits": the number of objects `max_objects` checks counts the objects of
  the cross-reference too; a page's count for `max_forms_per_page` can be
  refused once another page was read, as a colour can become unknown, since
  QPDF warns of an object it mis-read only when it reads it (decisions of the
  maintainer, 2026-10-02).
- "Text strings and names": a name with a `#` not followed by two hexadecimal
  digits has no bytes; a value that is such a name is unknown, and so are the
  graphics of an annotation one of whose appearance states is such a name,
  and every graphic of a page where PDFium draws an annotation whose
  `/Subtype` is such a name. "Fields and widgets": no name without bytes is
  compared with PDFium's.
- "Limits", "Paths and images": a name without bytes in a content stream
  leaves the page's form count refused where the count takes it, as the name
  operand of `Do`, `gs`, `scn`, `SCN` or `Tf`, and its colours unknown where
  the pattern check takes it, as the name operand of `Do`, `scn`, `SCN`, `cs`
  or `CS`, as a mis-read object does; QPDF's warning about it names the
  content stream and an offset, and where `/Contents` is an array no warning
  reaches the document (asked for by the maintainer in the prompt of
  2026-10-03).
- "Limits": a known gap stated: `open()` decodes the object streams whole
  before `max_objects` is checked, in memory about three times the decoded
  size of the largest, which `max_bytes` does not bound (decision of the
  maintainer, 2026-10-03).
- "Object identity": a check on open that both engines read the file as
  encrypted, with the same revision of the security handler, or both as not
  (decision of the maintainer, 2026-10-04).
- "Inspection": what `inspect()` reads and how, with no limit of its own
  (decision of the maintainer, 2026-10-03); permissions reported, not
  enforced. `docs/architecture.md`: the feature package is `inspection/`, so
  that it is not named as the function `inspect()` is (decision of the
  maintainer, 2026-10-03). "Object identity": the inspection is the exception
  to the check before a value from QPDF is used for a page or an annotation
  (decision of the maintainer, 2026-10-04).
- "Decryption": what `decrypt()` refuses, the crypt filters and the objects
  the engines decrypt otherwise among them, and what it checks: each page's
  geometry, annotation dictionaries, characters and rendering (decisions of
  the maintainer, 2026-10-02 and 2026-10-04); permissions not enforced.
  "Errors": `NotEncryptedError` and `DecryptionFailedError`.
- "Errors": a password is given to both engines as PDFium takes it, the
  conversion PDFium makes for the file's revision included, with four known
  gaps: a user and an owner password in different forms of one text, a
  password longer than 127 bytes from revision 5 on, password bytes that
  hold a NUL below revision 5, and a file whose `/V` and `/R` disagree;
  "Decryption": the known gap of a password outside ASCII is closed
  (decisions of the maintainer, 2026-10-04).
- "Decryption": `decrypt()` also compares the fields as QPDF reads them for
  `fields()` and `widgets()`: the field tree of `/AcroForm` and each page's
  widgets, their own entries and their `/Parent` chains, each object by the
  order in which it is first met, since QPDF numbers the objects anew; a
  result that gives other fields is refused with `DecryptionFailedError`;
  `max_items_per_page` holds QPDF's count of a page's `/Annots` too (decision
  of the maintainer, 2026-10-05).
- "Paths and images": where a pattern colour is set, the colour of a path's
  fill or stroke is known where QPDF reads it, set by `g` or `rg` or black, as
  PDFium reports it, and it is neither the grey of 191 nor the white PDFium
  makes up for a pattern, which stay unknown wherever a pattern colour is set;
  unknown otherwise. It was unknown for every path of the content or
  appearance. Where QPDF's paths are not PDFium's one for one, or the content
  holds what the engines may read otherwise, every colour of the content is
  unknown, as before; a known gap is stated, where the engines read a file
  otherwise without either one warning. The parts of a page's `/Contents` are
  joined with a space, as PDFium joins them, where they were joined with a
  line break; this holds for the pattern check and the count of
  `max_forms_per_page` too (decisions of the maintainer, 2026-10-05 and
  2026-10-06).
- "Rendering" states what is drawn: the content and the annotations' normal
  appearances as PDFium draws them by default; not an annotation flagged
  hidden or not to be viewed, a pop-up, or a widget, so form fields are not
  drawn. This was so before and was not written down.
- "Damaged files" states that iterating `Document.pages` raises at a damaged
  page and stops there, the pages after it read by their index (decision of
  the maintainer, 2026-10-06). This was so before and was not written down.

### Corrected

- `max_forms_per_page` (#56) counted too little where a file writes in the
  place of a dictionary or a name what PDFium reads leniently, and where QPDF
  read an object otherwise than PDFium: such a page could pass the check
  while PDFium parsed far more. #62 corrected the count.
- `docs/conventions.md` stated that unrounded values differ between platforms
  with one and the same engine. That was measured with the reference engine,
  not with PDFium. The section now lists what was measured and what was not.
- "Object identity" said the page-to-object mapping held for all 7935
  widgets in the corpus; the corpus has 7934 (7935 was the first
  measurement, on 109 files, five of them test files).
- A glyph's direction came from the C library's `atan2`, in radians turned
  into degrees: on the corpus it gave one glyph a direction on macOS that
  differed in its last bit from the one on Linux, and two glyphs on every
  platform a direction one bit off the angle of its unit vector along the
  baseline, rounded once. Words and lines took the vector along the baseline
  from the C library's `sin` and `cos`. Both are now computed in decimal
  arithmetic and rounded once, the same on every platform.
- A name with a `#` not followed by two hexadecimal digits, which QPDF reads
  with a warning, made `widgets()` and `fields()` fail with `EngineError`
  wherever it was a value they read, and `paths()` and `images()` where it was
  an annotation's `/Subtype`. Others, such as `/T#4#31` or one that ends in
  `#` and a hexadecimal digit, were read as bytes neither engine reads, and so
  was an appearance state named so. Such a name is now an unknown value,
  invalid in the file.
- A name with a `#` not followed by two hexadecimal digits in a content
  stream, where the count behind `max_forms_per_page` or the check that
  decides whether colours are known read it, was read as QPDF holds it, which
  PDFium does not: QPDF's warning about it names no object in the form issue
  #62's rule looks for, so the page stayed countable and the check behind the
  colours still answered it — for `scn` and `SCN` it answered unknown colours
  even before. Such a name now leaves the page uncountable
  (`LimitUncheckedError` where `max_forms_per_page` is set) and the check
  unanswered, its colours unknown, as a mis-read object does.
- A password outside ASCII that PDFium opened a file with did not open it in
  QPDF where PDFium converted it: QPDF was given only the password's UTF-8
  bytes. `open()` gave every value from QPDF as unknown, `recover()` refused
  the password and `decrypt()` the file. QPDF is now given the bytes PDFium
  converts the password to, at the revisions PDFium converts it for. And from
  revision 5 on, QPDF opened a file given a password longer than 127 bytes
  whose first 127 bytes are the file's, which PDFium refuses; `recover()`
  then raised `RecoveryFailedError`. Such a password no longer counts in
  QPDF, and `recover()` raises `PasswordRequiredError`; where PDFium opens the
  file with it, as the owner password where the user password is its first
  127 bytes, every value from QPDF is now unknown, `decrypt()` refuses the
  file and `recover()` the password, as the second known gap of "Errors"
  states.
- The check that decides whether a pattern colour is set missed some, and the
  colour PDFium makes up was given as the path's: a Pattern colour space
  selected through a default colour space — where `cs` selects `/DeviceRGB`
  and the resources give `/DefaultRGB` as one, PDFium reports white or black;
  `/DefaultGray` and `/DefaultCMYK` are read alike — or one whose family is
  the string `(Pattern)`, a base following; and a pattern set in `/Contents`,
  a part of it, or a form, that an object stream holds as what QPDF reads as a
  dictionary and PDFium as a stream: where the check meets one, it now has no
  answer, and the colours are unknown. A `/ColorSpace` resource named
  `/DeviceRGB`, which PDFium does not look up, no longer counts as a Pattern
  colour space.
- `max_forms_per_page` did not see `/Contents`, or a part of it, that an
  object stream holds as what QPDF reads as a dictionary and PDFium as a
  stream: such a page is now unchecked, as a form so held already left it.
- A warning QPDF gives as it reads an annotation's appearance, that it
  misread an object, was not heeded for that appearance, whose colours were
  given; they are now unknown.
- "Paths and images" named closed pop-ups among what a rendering does not
  show; no pop-up is drawn, open or closed (measured, 2026-10-06).

## 0.0.1

Project skeleton: pinned engines, `engine_info()`, import checks, corpus
manifest.

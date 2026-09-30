# Changelog

Every change to the public interface or to a convention is recorded here.
Versions are 0.x until the first consumer has switched; until then a change
may break callers.

## Unreleased

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
  reference `GraphicRef`. Nothing returns them yet.

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

### Corrected

- `docs/conventions.md` stated that unrounded values differ between platforms
  with one and the same engine. That was measured with the reference engine,
  not with PDFium. The section now lists what was measured and what was not.
- "Object identity" said the page-to-object mapping held for all 7935
  widgets in the corpus; the corpus has 7934 (7935 was the first
  measurement, on 109 files, five of them test files).

## 0.0.1

Project skeleton: pinned engines, `engine_info()`, import checks, corpus
manifest.

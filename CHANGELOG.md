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
  header, `DamagedFileError` when anything but white space follows the last
  `%%EOF`. From PDFium: `PasswordRequiredError`, `DamagedFileError` (also when
  it finds no page or has to rebuild the cross-reference table),
  `EngineError`, and `LimitExceededError` for more than `max_pages`. Data that
  is not `bytes`, a password that is not a `str` and limits that are not a
  `Limits` raise `TypeError`. Nothing is repaired.
- `Limits` with `max_bytes` and `max_pages`; none is set by default.

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

- A file counts as damaged also when anything but white space follows its
  last `%%EOF`, when PDFium can read it only by rebuilding its cross-reference
  table, and when PDFium finds no page in it.
- The font policy is installed before the first document is opened, and
  `engine_info()` names it in `font_policy`, because it changes the facts.
- The transform from user space to display space is the table under
  "Coordinate spaces"; display space is measured in user space units, and
  `UserUnit` does not scale it.
- Three answers: `None` for absent, `Unknown(reason)` for unknown, the
  capability table and an error for unsupported.
- Damaged files are recovered by an explicit step. `open` does not change what
  it reads.
- The two engines are checked against each other at run time. A value that
  depends on a failed check is unknown.
- Limits: the library checks size, page count, object count, items per page
  and a deadline between pages. Time and memory are the caller's
  responsibility. There are no built-in numbers.
- Rendering returns raw bytes. No type of an imaging library appears in the
  public interface.
- There are two coordinate spaces, user and display. There is no third space
  with y pointing downwards.
- The digest is a tool under `bench/`, not part of the public interface.

### Corrected

- `docs/conventions.md` stated that unrounded values differ between platforms
  with one and the same engine. That was measured with the reference engine,
  not with PDFium. The section now lists what was measured and what was not.

## 0.0.1

Project skeleton: pinned engines, `engine_info()`, import checks, corpus
manifest.

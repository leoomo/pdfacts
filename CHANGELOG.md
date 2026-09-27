# Changelog

Every change to the public interface or to a convention is recorded here.
Versions are 0.x until the first consumer has switched; until then a change
may break callers.

## Unreleased

### Added

- Error types: `PdfactsError` and its subclasses, listed under "Errors" in
  `docs/conventions.md`.
- `Unknown` and `UnknownReason` for values that could not be determined.
- The capability table: `Capability`, `capabilities()`, `require()`. No
  capability is available yet; each becomes available with the change that
  implements it.
- Geometry: `Point`, `Rect`, `Matrix` and `PageGeometry`, with the transforms
  between user space and display space listed under "Coordinate spaces" in
  `docs/conventions.md`.
- The runtime: `configure(font_policy=...)` and `FontPolicy`. The policy
  "embedded-only" ignores fonts installed on the host; "host" exists and is not
  supported. `EngineInfo` has a new field `font_policy`, and the capability
  `FONT_POLICY_EMBEDDED_ONLY` is available.

### Changed

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

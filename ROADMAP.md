# Roadmap

Each milestone has a gate. A milestone is done when its gate has been run and
the result recorded, not when the code looks finished.

Order: M0, M1, then M2 and M3 in either order, M4, M5, M6. M7 has no date.

| Milestone | Content |
| --- | --- |
| M0 | project skeleton, pinned engines, corpus manifest |
| M1 | documents, geometry, determinism, extension skeleton |
| M2 | annotations and forms |
| M3 | paths and images |
| M4 | text: glyphs, words, lines |
| M5 | inspection, edit session, repair |
| M6 | integration into the first consumer |
| M7 | checks before going public |

## M0 Project skeleton

Deliverables
- repository skeleton with pinned engines and a lock file
- `THIRD_PARTY.md`
- corpus manifest and fetch script
- comparison tooling (`bench/`), to follow once there is something to compare

Gate
- the wheel installs and runs in a clean environment on Linux x86_64, Linux
  arm64 and macOS arm64

Recorded results are in [docs/capabilities.md](docs/capabilities.md).

## M1 Documents, geometry, determinism

Deliverables
- open and close, page geometry, coordinate transforms, font policy, error
  hierarchy, resource limits, rendering
- extension skeleton: object references, backend protocols, runtime,
  capability table

Gate
- constructed geometry cases are all correct: rotation 0/90/180/270, crop box,
  shifted origin, reversed corners, inherited page boxes, UserUnit
- the fact digest of every corpus file is identical on three platforms
- invalid input maps to a specific error: empty, header only, garbage, password
  required
- a truncated file is readable after a QPDF rewrite; a failed rewrite is
  reported as a failure, never as an empty result
- the architecture check passes

## M2 Annotations and forms

Deliverables
- annotation base type and reader: subtype, rectangle, flags, contents, colour,
  appearance, object reference
- field tree, widgets, inherited flags, values, options, appearance states,
  border and background

Gate
- widget rectangle, name, type, flags and label equal the file content for
  every widget in the corpus
- options and states of radio buttons and check boxes equal the file content
- every annotation in the corpus yields its common properties
- object references are stable across repeated reads

## M3 Paths and images

Deliverables
- paths from page content and from appearance streams, nested form XObjects,
  stroke and fill, image placements

Gate
- the rules that turn path segments into lines, rectangles, quads and curves
  are documented and covered by constructed cases
- constructed cases with nested transforms and repeated images are correct

## M4 Text

The main risk of the project.

Deliverables
- glyph facts, word and line segmentation, versioned rule sets, rotated text,
  normalization

Before starting
- constructed cases that pin every segmentation threshold
- a time-boxed comparison of two glyph sources: the PDFium text interface and
  a content-stream interpreter built on QPDF tokens

Gate
- word boxes checked against rendering are correct for at least 99% of words
- median time per file is at most 1.5 times that of the reference engine

## M5 Inspection, edit session, repair

Deliverables
- object counts, declared stream lengths, encryption details, active content
  report
- edit session with verification after writing; the first operations are
  rewrite and decrypt only

Gate
- owner-password-protected corpus files are writable by a third-party library
  after repair, with field names and rectangles unchanged
- a page `/Contents` entry that is an indirect array is handled
- verification after writing detects a deliberately introduced fault

## M6 Integration

The first consumer switches to pdfacts. Its acceptance criteria live in that
project.

## M7 Before going public

- a license has been chosen and its text is in the repository
- provenance review against [docs/provenance.md](docs/provenance.md)
- the history contains no secrets, private samples or user data
- documentation, examples, versioning and deprecation policy, security contact
- contribution policy

## After 1.0

Writing annotations, filling and flattening forms, page operations, outlines,
links, attachments, metadata, redaction. The structure for these is described
in [docs/architecture.md](docs/architecture.md).

# Roadmap

Each milestone has a gate. A milestone is done when its gate has been run and
the result recorded, not when the code looks finished.

Order: M0, M1, text probe, M2, M3, M4, M5, M6. M7 has no date.

| Milestone | Content | State |
| --- | --- | --- |
| M0 | project skeleton, pinned engines, corpus manifest | done 2026-09-27 |
| M1 | documents, geometry, determinism, test and gate tooling | next |
| Text probe | two glyph sources compared, time-boxed | planned |
| M2 | annotations and forms | planned |
| M3 | paths and images | planned |
| M4 | text: glyphs, words, lines | planned |
| M5 | inspection, edit session, repair | planned |
| M6 | integration into the first consumer; version 1.0 | planned |
| M7 | checks before going public | no date |

The first consumer decides the priorities. What it does not need waits until
after M6.

## Supported platforms

| Platform | How it is verified |
| --- | --- |
| Linux x86_64 and arm64, glibc | M0 gate run in containers; continuous integration from M1 on |
| macOS arm64 | on the maintainer's machine, with the same gate script |
| Linux with musl, Windows | not verified; wheels of both engines exist, nothing was run |

Python 3.13 or newer.

## Versions

Versions are 0.x until M6. Before 1.0 the interface may change in ways that
break callers; every such change is recorded in [CHANGELOG.md](CHANGELOG.md).
From 1.0 on, fact structures only grow (rule 7 in
[docs/architecture.md](docs/architecture.md)).

## M0 Project skeleton

Deliverables
- repository skeleton with pinned engines and a lock file
- `THIRD_PARTY.md`
- corpus manifest and fetch script

Gate
- the wheel installs and runs in a clean environment on Linux x86_64, Linux
  arm64 and macOS arm64

Recorded results are in [docs/capabilities.md](docs/capabilities.md).

## M1 Documents, geometry, determinism

Deliverables
- open and close; facts stay usable after the document is closed, page and
  document objects do not
- page geometry and the transforms between user space and display space
- font policy: fonts installed on the host are ignored
- error hierarchy
- values that can be unknown, and the capability table
- resource limits that the library can check between two engine calls
- rendering of a page or a region to raw bytes
- explicit recovery of damaged files
- a check that both engines describe the same document
- the parts of the extension skeleton that these features call: runtime, page
  references, backend protocols for geometry, rendering and object access
- test tooling: a PDF builder that writes bytes directly, so that no engine
  takes part in constructing a test file
- continuous integration on Linux x86_64 and Linux arm64
- gate scripts and the digest tool under `bench/`
- the import check covers `tests/` and `tools/` as well as the library
- a one-page design note on how form filling can be added later by adding
  modules only; no code

Gate
- constructed geometry cases are all correct: rotation 0/90/180/270, crop box,
  shifted origin, reversed corners, inherited page boxes, UserUnit
- glyph data read through the PDFium backend is identical on three platforms
  for every corpus file; this is what verifies the font policy
- digests of unrounded values are compared on three platforms and the result
  decides how the digest is built (see [docs/conventions.md](docs/conventions.md))
- invalid input maps to a specific error: empty, header only, garbage, password
  required
- a truncated file is reported as damaged by `open`, and is readable after
  explicit recovery; a failed recovery is reported as a failure, never as an
  empty result
- the architecture check passes

## Text probe

Time-boxed to two weeks. It comes before M2 because text is the main risk of
the project and the result shapes the architecture.

Two glyph sources are compared on the same files: the PDFium text interface,
and a content-stream interpreter built on QPDF tokens.

The decision rule is fixed before the probe starts. The content-stream
interpreter is adopted only if all three hold:

- it changes at least two percentage points fewer results in the first
  consumer's comparison than the PDFium text interface does
- median time per file is at most 1.5 times that of the reference engine
- no corpus file is readable through the PDFium text interface but not through
  the interpreter

Otherwise the PDFium text interface is used, and spaces it leaves out are
inferred from glyph spacing.

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
- the result of the text probe

Gate
- word boxes checked against rendering are correct for at least 99% of words
- median time per file is at most 1.5 times that of the reference engine

If the gate is missed, in this order:

1. the cases that do not work are recorded as unsupported, and the other
   milestones continue
2. the gate is relaxed, and every remaining difference is reviewed one by one
3. a third text source is added

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
project. After each earlier milestone the consumer's comparison is run again
outside this repository, so that the interface meets a real caller early.

Version 1.0 is released with this milestone.

## M7 Before going public

- a license has been chosen and its text is in the repository; the intended
  license is Apache-2.0
- provenance review against [docs/provenance.md](docs/provenance.md)
- the history contains no secrets, private samples or user data
- documentation, examples, versioning and deprecation policy, security contact
- contribution policy
- a comparison with other libraries on the same files
- a corpus that goes beyond official German forms
- Windows and musl are either verified or stated as unsupported

## After 1.0

Writing annotations, filling and flattening forms, page operations, outlines,
links, attachments, metadata, redaction. The structure for these is described
in [docs/architecture.md](docs/architecture.md).

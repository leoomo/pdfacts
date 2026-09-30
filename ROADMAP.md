# Roadmap

Each milestone has a gate. A milestone is done when its gate has been run and
the result recorded, not when the code looks finished.

The first consumer decides the priorities. The order below brings it to the
point where it can switch to pdfacts as early as possible: first for documents
with form fields, later for documents without.

| Order | Milestone | Content | State |
| --- | --- | --- | --- |
| 1 | M0 | project skeleton, pinned engines, corpus manifest | done 2026-09-27 |
| 2 | M1 | documents, geometry, determinism, test and gate tooling | done 2026-09-29 |
| 3 | M2 | forms: fields and widgets | done 2026-09-30 |
| 4 | M3 | paths and images | planned |
| 5 | M4a | text, first level | planned |
| 6 | M5a | inspection, repair and decryption | planned |
| 7 | M6 | the first consumer switches for documents with form fields; version 1.0 | planned |
| 8 | M4b | text probe, then text in full | planned |
| 9 | M5b | what was left out on the way: edit session, annotations, rotated text | planned |
| - | M7 | checks before going public | no date |

Until M6 nothing is built that the switch does not need.

## Supported platforms

The checks of every change run on all three with `tools/checks.py`.

| Platform | How it is verified |
| --- | --- |
| Linux x86_64 and arm64, glibc | in containers on the development machine; x86_64 is emulated, nothing runs on real x86_64 hardware |
| macOS arm64 | natively on the development machine |
| Linux with musl, Windows | not verified; wheels of both engines exist, nothing was run |

Python 3.13 or newer.

## Versions

Versions are 0.x until M6. Before 1.0 the interface may change in ways that
break callers; every such change is recorded in [CHANGELOG.md](CHANGELOG.md).
From 1.0 on, fact structures only grow (rule 7 in
[docs/architecture.md](docs/architecture.md)), and a change that breaks
callers needs a new major version.

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
- one script that runs the checks on macOS, Linux arm64 and Linux x86_64
  (`tools/checks.py`); the repository uses no hosted continuous integration
- gate scripts and the digest tool under `bench/`
- the import check covers `tests/` and `tools/` as well as the library

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

Recorded results are in [docs/capabilities.md](docs/capabilities.md).

## M2 Forms

Deliverables
- field tree, widgets, inherited flags, values, options, appearance states,
  border and background
- for every widget: rectangle, object reference, and the field it belongs to

Annotations other than widgets are read in M5b. M5a looks at their
dictionaries only as far as the active content report needs it.

Gate
- widget rectangle, name, type, flags and label equal the file content for
  every widget in the corpus
- options and states of radio buttons and check boxes equal the file content
- object references are stable across repeated reads

Recorded results are in [docs/capabilities.md](docs/capabilities.md).

## M3 Paths and images

Deliverables
- paths from page content and from appearance streams, nested form XObjects,
  stroke and fill, image placements

Gate
- the rules that turn path segments into lines, rectangles, quads and curves
  are documented and covered by constructed cases
- constructed cases with nested transforms and repeated images are correct

## M4a Text, first level

Text as far as a consumer needs it to find the label next to a form field. It
is not yet good enough to find the fields of a document that has no form
fields; that is M4b.

Deliverables
- glyph facts read through the PDFium text interface
- words and lines, built with one documented rule set
- normalization of what the engine returns: the line-end hyphen marker and
  expanded ligatures

Not in M4a: rotated text, a second glyph source, tuning of thresholds.

Gate
- constructed cases pin every rule of the rule set
- glyph, word and line facts of every corpus file are identical on three
  platforms
- what the rule set is known to get wrong is listed in
  [docs/capabilities.md](docs/capabilities.md), with counts from the corpus

## M5a Inspection, repair and decryption

Deliverables
- inspection report: object counts, declared stream lengths, encryption
  details, active content
- two functions that return new bytes: recovery, which exists since M1, and
  decryption; each verifies its own result before it returns

Not in M5a: the edit session. The two functions are written so that they can
be moved onto it later without changing their signature.

Gate
- owner-password-protected corpus files are readable and writable by a
  third-party library after decryption, with field names and rectangles equal
  to those of the original file
- a page `/Contents` entry that is an indirect array is handled
- the verification inside each function detects a deliberately introduced fault
- the active content report names the object in which each finding was made

## M6 Integration

The first consumer switches to pdfacts for documents with form fields. Its
acceptance criteria live in that project. After each earlier milestone the
consumer's comparison is run again outside this repository, so that the
interface meets a real caller early.

Version 1.0 is released with this milestone.

## M4b Text in full

The main technical risk of the project. It comes after M6 so that the switch
does not depend on it.

It starts with a probe, time-boxed to two weeks. Two glyph sources are
compared on the same files: the PDFium text interface, and a content-stream
interpreter built on QPDF tokens.

The decision rule is fixed before the probe starts. The content-stream
interpreter is adopted only if all three hold:

- it changes at least two percentage points fewer results in the first
  consumer's comparison than the PDFium text interface does
- median time per file is at most 1.5 times that of the reference engine
- no corpus file is readable through the PDFium text interface but not through
  the interpreter

Otherwise the PDFium text interface is used, and spaces it leaves out are
inferred from glyph spacing.

Deliverables
- word and line segmentation with versioned rule sets, thresholds pinned by
  constructed cases
- the glyph source chosen by the probe

Gate
- word boxes checked against rendering are correct for at least 99% of words
- median time per file is at most 1.5 times that of the reference engine

If the gate is missed, in this order:

1. the cases that do not work are recorded as unsupported
2. the gate is relaxed, and every remaining difference is reviewed one by one
3. a third text source is added

## M5b What was left out on the way

- edit session with verification after writing; rewrite and decrypt become
  operations of it
- annotation base type and reader for annotations other than widgets: subtype,
  rectangle, flags, contents, colour, appearance, object reference
- rotated text
- a one-page design note on how form filling can be added later by adding
  modules only; no code

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

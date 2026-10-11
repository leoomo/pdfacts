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
| 4 | M3 | paths and images | done 2026-10-01 |
| 5 | M4a | text, first level | done 2026-10-02 |
| 6 | M5a | inspection, repair and decryption | done 2026-10-05 |
| 7 | M4b | text probe, then text in full | done 2026-10-09 |
| 8 | M6 | the first consumer switches for documents with form fields; version 1.0 | releases 0.1.0 and 0.2.0 made; the switch waits in the first consumer's project |
| 9 | M5b | what was left out on the way: edit session, annotations, rotated text | done 2026-10-10 |
| - | M7 | checks before going public | preparation begun 2026-10-10 (#148); the switch and 1.0 come first |

Until M6 nothing is built that the switch does not need, except M4b: on
2026-10-06, with the switch waiting in the first consumer's project, the
maintainer moved it ahead of the switch and of version 1.0.

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

Recorded results are in [docs/capabilities.md](docs/capabilities.md).

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

Recorded results are in [docs/capabilities.md](docs/capabilities.md).

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

Recorded results are in [docs/capabilities.md](docs/capabilities.md).

## M4b Text in full

The main technical risk of the project. The switch does not depend on it.
It was planned after M6; it comes before the switch and before version 1.0
(decision of the maintainer, 2026-10-06), so that what the probe decides
about text can still change the interface without a major version.

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

The probe ran on 2026-10-06 and 2026-10-07. Its prototype is on the branch
`probe/m4b-text-interpreter`, under `bench/probes/m4b_text`, and is not
merged.

Before the prototype was written, the differences that text makes in the
consumer's comparison were attributed. They come from three things:
- line boxes that leave out the tabs and spaces after a label, glyphs that
  PDFium's text interface does not give;
- the heights of glyph boxes;
- rotated text.

How M4a groups glyphs into words and lines was not among them.

Criteria 1 and 3 were measured under the glyph boxes and line boxes of the
second rule set below:
- **Criterion 1** holds.
- **Criterion 3** holds: the prototype reads all 467 pages of the corpus,
  and PDFium reads them all too.
- **Criterion 2** was timed per corpus file, on macOS arm64, against the
  reference engine's text extraction. What was timed is the prototype's
  glyphs, made into pdfacts' glyph facts and into words and lines by M4a's
  rules; opening the document with `pdfacts.open` was not in it.
  - Glyph facts made with every coordinate checked took 1.47 to 1.53 times
    the reference engine's median time, in two runs.
  - Made without per-value checks, they took 1.17 times, and were equal
    page by page.
  - pdfacts as released, timed through `pdfacts.open` and `lines()`, took
    2.34 times.

The maintainer counted criterion 2 without per-value checks, since that cost
is pdfacts' own whatever the glyph source, and adopted the interpreter
(2026-10-07).

Deliverables
- versioned rule sets for glyphs, words and lines, thresholds pinned by
  constructed cases:
  - `TextRules.V1` is M4a's rule set, unchanged, and is the default of
    `glyphs()`, `words()` and `lines()`;
  - `TextRules.V2` comes through their `rules` keyword.
- `TextRules.V2`:
  - glyphs read from the content stream through QPDF's tokens, white space
    included;
  - a glyph box from the origin to the advance, at least one em tall;
  - line boxes that take the white space on the line;
  - upright text only, since rotated text is M5b's, as `TextRules.V3`.
- the text facts that pdfacts computes itself are made without per-value
  checks; a caller who makes a fact keeps every check

Gate, for `TextRules.V2`
- word boxes checked against rendering are correct for at least 99% of
  words. The check also compares the one-em glyph boxes with boxes of the
  font descriptor's height; if the descriptor's height does better, the
  question goes back to the maintainer (2026-10-07).
- median time per file, from `pdfacts.open` to the page's lines, is at most
  1.5 times that of the reference engine
- PDFium's characters are found among V2's glyphs on the corpus, at the same
  origin and with the same text. The glyphs V2 has and PDFium leaves out,
  such as white space and text drawn again at its place, are counted apart.
  What does not agree is listed in [docs/capabilities.md](docs/capabilities.md)
  with counts. The check runs at the gate, not at run time.
- glyph, word and line facts of V2 of every corpus file are identical on
  three platforms

If the gate is missed, in this order:

1. the cases that do not work are recorded as unsupported
2. the gate is relaxed, and every remaining difference is reviewed one by one
3. a third text source is added

Recorded results are in [docs/capabilities.md](docs/capabilities.md).

## M6 Integration

The first consumer switches to pdfacts for documents with form fields. Its
acceptance criteria live in that project. After each earlier milestone the
consumer's comparison is run again outside this repository, so that the
interface meets a real caller early.

Version 1.0 is released with this milestone. Two releases were made in it on
2026-10-06: 0.1.0, the release the consumer switches on, and 0.2.0, the
interface as the review before 1.0 left it. The switch itself waits in the
first consumer's project.

## M5b What was left out on the way

Begun on 2026-10-09, while the first consumer's switch stays parked in its
project (#137, the maintainer's decision of that day; the switch and 1.0
wait in #106). The sub-issues, in the order #137 proposes: B1 annotations
other than widgets, B2 `TextRules.V3`, B3 the edit session, B4 the design
note #15, written from the interface as it is then. All four were merged on
2026-10-10: B1 as #140, B2 as #142, B3 as #144 and B4 as #145; the note's
answer is that the list of existing public names a write feature would have
to change is empty. M5b was planned with no gate of its own (#137); the
record of its facts is in
[docs/capabilities.md](docs/capabilities.md), the evidence each feature's
pull request states. The three-platform identity of those facts, which the
record left open, was proven the same day by the M5b gate
(`bench/gates/m5b.py`), whose run is recorded there as well.

- edit session with verification after writing; rewrite and decrypt become
  operations of it
- annotation base type and reader for annotations other than widgets: subtype,
  rectangle, flags, contents, colour, appearance, object reference
- rotated text, as `TextRules.V3`
- a one-page design note on how form filling can be added later by adding
  modules only; no code

## M7 Before going public

Prepared from 2026-10-10 while the first consumer's switch stays parked in
its project (#106) and this repository's work stands done through M5b: the
items that need no other project — the audits of the history and of the
provenance, the contribution policy, the documents of versioning and
contact, the platforms statement, and the license once the maintainer
confirms the choice — are prepared in the sub-issues of #148. The
comparison with other libraries and the corpus beyond official German
forms wait for M7 itself; the switch and version 1.0 still come first.

- a license has been chosen and its text is in the repository; the intended
  license is Apache-2.0
- provenance review against [docs/provenance.md](docs/provenance.md) (done 2026-10-10, #152)
- the history contains no secrets, private samples or user data (audited
  2026-10-10, #150)
- documentation, examples, versioning and deprecation policy, security contact
- contribution policy (written 2026-10-11, #154)
- a comparison with other libraries on the same files
- a corpus that goes beyond official German forms
- Windows and musl are either verified or stated as unsupported

## After 1.0

Writing annotations, filling and flattening forms, page operations, outlines,
links, attachments, metadata, redaction. The structure for these is described
in [docs/architecture.md](docs/architecture.md).

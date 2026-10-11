# pdfacts

[![Python 3.13+](https://img.shields.io/badge/python-3.13%2B-3776AB.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/License-MIT-informational.svg)](LICENSE)
[![Code style: ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)

Structured, verifiable facts from PDF files: text, paths, images,
annotations and forms — each with explicit geometry, each identical on
every platform, on two engines.

pdfacts reads what a PDF *is*, not what one renderer makes of it. The facts
it returns — glyphs and their boxes in user space, words and lines under
three rule sets, paths with the colours the file sets, form fields with
their values as written, annotations with the appearance their `/AP`
selects, encryption and active content as a report — are either read from
the file or refused with a reason. What cannot be determined is `Unknown`
with a reason, never a default that looks like a real value.

It does not parse PDF syntax, render pages or rasterize fonts itself. Those
jobs belong to the engines it stands on.

> **Status: public, pre-1.0.** The reading side is complete through
> milestone M5b and proven on a corpus of 104 real documents on three
> platforms; the writing side holds the edit session. Before 1.0 the
> interface may change in ways that break callers — see
> [Versioning](#versioning) and [ROADMAP.md](ROADMAP.md) for what is
> planned and how each step is accepted.

## Contents

- [Why pdfacts](#why-pdfacts)
- [Installation](#installation)
- [Quick start](#quick-start)
- [The facts of a page](#the-facts-of-a-page) — [text](#text), [forms](#forms),
  [paths and images](#paths-and-images), [annotations](#annotations),
  [inspection](#inspection), [recovery](#recovery), [decryption](#decryption),
  [the edit session](#the-edit-session)
- [Two engines, one answer](#two-engines-one-answer)
- [Nothing is made up](#nothing-is-made-up)
- [Every value identical on every platform](#every-value-identical-on-every-platform)
- [Supported platforms](#supported-platforms)
- [Interface](#interface)
- [Versioning](#versioning)
- [Documentation](#documentation)
- [Development and checks](#development-and-checks)
- [Contributing](#contributing)
- [Security](#security)
- [License](#license)

## Why pdfacts

The mature PDF engines — PDFium, QPDF — leave the interpretation to the
caller. pdfacts adds what they leave out:

- **Facts with explicit geometry.** Glyphs, words, lines, paths, images,
  annotations and form widgets in a stated coordinate space, with the
  transform to the displayed page. A box is never "roughly right"; it is
  in user space, and you are told which space it is in.
- **Form data you can trust.** Fields, widgets, values, options and states
  taken from the file itself, not normalised into a generic model that
  quietly loses what the file wrote.
- **Three rule sets for text.** `TextRules.V1` reads PDFium's characters,
  `V2` the codes the content stream itself shows, white space included,
  `V3` holds the rotated glyphs in lines too. Same call, stated rules,
  comparable results.
- **Inspection and repair.** Encryption, active content and object counts
  as a report; rewriting and decryption as explicit, verified operations —
  and an edit session that applies several in one checked write.
- **The three answers.** Every value is either read from the file,
  `Unknown` with a reason, or an error. Nothing is guessed, defaulted or
  silently dropped.

## Installation

Python 3.13 or newer. The engines are dependencies and their versions are
pinned exactly — extracted facts depend on the engine build, so an upgrade
is a reviewed change, never a side effect.

The package is not on a public index yet; install it from a checkout:

```bash
git clone https://github.com/leoomo/pdfacts.git
cd pdfacts
uv sync        # or: pip install .
```

```python
import pdfacts

print(pdfacts.engine_info())  # the versions your facts depend on
```

## Quick start

Keep the bytes: every fact describes them.

```python
from pathlib import Path

import pdfacts

data = Path("form.pdf").read_bytes()  # keep the bytes: the facts describe them
with pdfacts.open(data) as document:
    for page in document.pages:
        geometry = page.geometry  # user space, with the transform to display space
        print(page.index, geometry.visible_box, geometry.rotation, page.ref.object_number)
    print(document.warnings)

    # A region in user space at 3 pixels per unit: grey bytes, row 0 at the top.
    bitmap = document.pages[0].render(3, region=pdfacts.Rect(50, 40, 110, 60))
    print(bitmap.width, bitmap.height, bitmap.row_length, bitmap.display_to_pixel)

print(pdfacts.engine_info())  # the versions the facts depend on
```

`open()` repairs nothing. A damaged file is recovered in a step of its own,
which writes new bytes:

```python
try:
    document = pdfacts.open(data)
except pdfacts.DamagedFileError:
    recovery = pdfacts.recover(data)  # rebuilt, written anew and checked
    print(recovery.page_count, recovery.warnings)  # pages may have been lost
    data = recovery.data  # new bytes, new object numbers: keep these
    document = pdfacts.open(data)
```

## The facts of a page

Each family is one call. The examples below run exactly as written.

### Text

The text of a page, third rule set — the rotated glyphs take part, each
line holding one direction:

```python
with pdfacts.open(data) as document:
    for word in pdfacts.words(document, 0, rules=pdfacts.TextRules.V3):
        print(word.text, word.box)  # user space

    for line in pdfacts.lines(document, 0, rules=pdfacts.TextRules.V3):
        print(line.text, line.box)  # the box holds the line's white space

    for glyph in pdfacts.glyphs(document, 0, rules=pdfacts.TextRules.V2):
        print(glyph.text, glyph.origin, glyph.direction, glyph.size)
```

`V1` (the default) reads PDFium's characters, which leave out about 12% of
the space glyphs; `V2` reads the codes the content itself shows, white
space included; `V3` is `V2` with the rotated glyphs held in lines. The
rule set is a stated choice, and the three are comparable glyph by glyph.

### Forms

Fields and widgets as the file writes them:

```python
with pdfacts.open(data) as document:
    for widget in pdfacts.widgets(document, 0):
        print(widget.field.name, widget.field.value, widget.rect)
        print(widget.field.type, widget.state, widget.states)
```

A field's value is what the file holds, an `Unknown` with a reason where
it cannot be read — never an invented default.

### Paths and images

What a page draws, with the colours the content sets:

```python
with pdfacts.open(data) as document:
    for path in pdfacts.paths(document, 0):
        print(path.stroke, path.line_width, len(path.subpaths))

    for image in pdfacts.images(document, 0):
        print(image.pixel_width, image.pixel_height, image.corners)
```

Where a pattern colour is set, a path's colour is unknown unless QPDF
confirms what PDFium reports — the two engines must agree, or the value
says why it does not.

### Annotations

The annotations other than widgets, with what the file writes:

```python
with pdfacts.open(data) as document:
    for annotation in pdfacts.annotations(document, 0):  # other than widgets
        print(annotation.ref.kind, annotation.rect, annotation.contents)
```

### Inspection

A file's structure, encryption and active content as one report:

```python
with pdfacts.open(data) as document:
    report = pdfacts.inspect(document)  # objects, encryption, active content
    print(report.objects.total, report.encryption, report.warnings)
```

Actions are listed with the entries that name them to run, XFA streams and
embedded files included — read as a report, so a caller can decide what to
do with what a file carries.

### Recovery

See the quick start: `pdfacts.recover(data)` rebuilds a damaged file,
writes it anew and checks the result; `recovery.data` are the bytes to
keep.

### Decryption

An encrypted file, written anew without its encryption:

```python
plain = pdfacts.decrypt(data)  # verified before it is returned
print(plain.page_count)
data = plain.data  # new bytes, no encryption: keep these
```

### The edit session

Several operations, one verified write:

```python
session = pdfacts.EditSession(data).add(pdfacts.Rewrite()).apply()
print(session.page_count, len(session.data))  # written anew, then checked
```

`Decrypt()` beside `Rewrite()` rebuilds *and* decrypts a damaged encrypted
file in one write — which neither `recover()` nor `decrypt()` can do alone.

## Two engines, one answer

| Engine | Binding | Used for |
| --- | --- | --- |
| PDFium | pypdfium2 | what needs interpretation: text positions of `TextRules.V1` (M4b), paths, rendering |
| QPDF | pikepdf | file structure: objects, dictionaries, streams, rewrite, decryption; the tokens of content streams, which pdfacts interprets for the colour state that confirms a pattern colour and for the text of `TextRules.V2` (M4b) |

Where both engines can answer, they are compared: the number of
annotations on a page, the widgets, the geometry. Where they disagree, the
fact is `Unknown` with the reason `engines-disagree` — the disagreement
itself is information, and it is never resolved by trusting one side
quietly.

## Nothing is made up

Every value pdfacts returns is one of three answers:

1. **A value, read from the file.** With its geometry, in a stated space.
2. **`Unknown`, with a reason.** `invalid-in-file` where the file holds
   what the PDF specification does not allow; `unreadable` where the
   engines cannot read it; `engines-disagree` where they differ;
   `not-read` where pdfacts does not read that kind of value yet.
3. **An error.** For a file that cannot be opened, a value that cannot be
   used, a limit that was set and reached.

Never a default that looks like a real value, never an empty result in
place of a failure, never mock or placeholder data. The text of an
`Unknown`'s reason is written for people, not to be parsed; the reason
itself is a stable enum value.

## Every value identical on every platform

Every change is checked on macOS arm64, Linux arm64 and Linux x86_64
(`tools/checks.py --all`; there is no hosted continuous integration — the
checks run on every change, and a pull request quotes its run). Beyond the
suite, the repository keeps *gates*: corpus runs over 104 real documents
whose every fact is digested and compared across the three platforms —
geometry, glyphs, words, lines, paths, images, annotations, inspection,
decryption, recovery — with per-file receipts recorded in
[docs/capabilities.md](docs/capabilities.md). Identical means identical:
same counts, same digests, file by file.

## Supported platforms

Python 3.13 or newer. pdfacts supports the platforms the checks of every
change cover:

| Platform | Support |
| --- | --- |
| macOS arm64 | supported; checked natively on the development machine |
| Linux x86_64 and arm64, glibc | supported; checked in containers, x86_64 emulated on an arm64 machine, on no real x86_64 hardware |
| Windows; Linux with musl | not supported: wheels of both engines exist, but nothing has been run on them |

## Interface

The interface is what the package `pdfacts` exports, the names in
`pdfacts.__all__`; the modules beneath it are not. Facts are frozen
dataclasses whose collections are tuples. They are carried between processes
by `pickle`, from and to the same version of pdfacts; `dataclasses.asdict()`
and JSON are no transport for them: JSON refuses the bytes a name holds, and
`asdict()` turns an `Unknown` into a plain mapping of its reason and detail.
The text of an `Unknown`'s detail, of a warning and of an error's message is
written for people, not to be parsed.

## Versioning

Before version 1.0 the interface may change in ways that break callers;
every such change is recorded in [CHANGELOG.md](CHANGELOG.md). From 1.0
on, fact structures only grow, and a change that breaks callers needs a
new major version.

## Documentation

- [Architecture](docs/architecture.md): layers and the rules that keep new features additive
- [Conventions](docs/conventions.md): coordinate spaces, boxes, identity, digests
- [Capabilities](docs/capabilities.md): what works, what does not, what is unknown — with the numbers
- [Provenance](docs/provenance.md): where knowledge came from and what may not be copied
- [Third-party components](THIRD_PARTY.md)
- [Changelog](CHANGELOG.md): every change to the interface or to a convention
- [Roadmap](ROADMAP.md): what is planned and how each step is accepted
- [Rules for coding agents](AGENTS.md)

## Development and checks

Dependencies are managed with [uv](https://docs.astral.sh/uv/) only:

```bash
uv sync
uv run pytest
uv run ruff check
uv run ruff format --check
uv run python tools/check_architecture.py
uv build
```

All of these, plus a test of the built wheel, on the three platforms, in
one run:

```bash
python3 tools/checks.py --all
```

A fresh clone passes the tests: the test suite builds its own PDFs and its
own corpora and needs nothing outside the repository. The corpus the gates
read (`bench/corpus/`) is not part of the repository; its files are never
committed, only listed in `bench/corpus/manifest.csv`, and the gates place
them from a read-only source of the project.

## Contributing

One issue, one branch, one pull request; the checks of every change; the
rules of [AGENTS.md](AGENTS.md). See
[CONTRIBUTING.md](CONTRIBUTING.md) for the short path.

## Security

Report security problems through GitHub's private vulnerability reporting,
not a public issue: [SECURITY.md](SECURITY.md).

## License

[MIT](LICENSE).

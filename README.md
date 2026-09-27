# pdfacts

Structured, verifiable facts from PDF files: text, paths, images, annotations
and forms, each with explicit geometry.

> **Status: private, pre-alpha (milestone M0).** The only public function today
> is `pdfacts.engine_info()`. Nothing else described below exists yet; see
> [ROADMAP.md](ROADMAP.md) for what is planned and how each step is accepted.

## What it is for

pdfacts sits on top of two mature engines and adds what they leave to the
caller:

- **Facts with explicit geometry.** Glyphs, words, lines, paths, images,
  annotations and form widgets, in a stated coordinate space, with the
  transform to the displayed page.
- **Form data you can trust.** Fields, widgets, values, options and states
  taken from the file itself.
- **Inspection and repair.** Encryption, active content and object counts as a
  report; rewrite and decryption as explicit operations.

It does not parse PDF syntax, render pages or rasterize fonts itself. Those
jobs belong to the engines.

## Engines

| Engine | Binding | Used for |
| --- | --- | --- |
| PDFium | pypdfium2 | text, geometry, rendering |
| QPDF | pikepdf | object access, rewrite, decryption |

Engine versions are pinned exactly in `pyproject.toml`. Extracted facts depend
on the engine build, so an upgrade is a reviewed change.

```python
import pdfacts

print(pdfacts.engine_info())
```

## Development

Python 3.11 or newer; the project is developed on 3.13. Dependencies are managed
with [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest
uv run ruff check
uv run python tools/check_architecture.py
uv build
```

## Layout

| Path | Content |
| --- | --- |
| `src/pdfacts/core/` | engine-neutral building blocks |
| `src/pdfacts/backends/` | the only code that talks to the engines |
| `tests/` | tests whose expected values come from construction, not from an engine |
| `tools/` | repository checks |
| `bench/` | corpus manifest and fetch script; not part of the wheel |
| `docs/` | architecture, conventions, capabilities, provenance |

## Documents

- [Architecture](docs/architecture.md): layers and the rules that keep new features additive
- [Conventions](docs/conventions.md): coordinate spaces, boxes, identity, digests
- [Capabilities](docs/capabilities.md): what works, what does not, what is unknown
- [Provenance](docs/provenance.md): where knowledge came from and what may not be copied
- [Third-party components](THIRD_PARTY.md)

## License

No license has been chosen yet. Until a license file is added to this
repository, all rights are reserved.

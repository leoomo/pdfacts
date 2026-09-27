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
| PDFium | pypdfium2 | what needs interpretation: text positions, paths, rendering |
| QPDF | pikepdf | file structure: objects, dictionaries, streams, rewrite, decryption |

Engine versions are pinned exactly in `pyproject.toml`. Extracted facts depend
on the engine build, so an upgrade is a reviewed change.

```python
import pdfacts

print(pdfacts.engine_info())
```

## Development

Python 3.13 or newer. Dependencies are managed with
[uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest
uv run ruff check
uv run ruff format --check
uv run python tools/check_architecture.py
uv build
```

## Checks

`tools/checks.py` defines the checks that decide whether a change may be
merged. It installs the locked dependencies, runs the commands above, builds
the wheel, installs it into a fresh environment outside the checkout and calls
`pdfacts.engine_info()` from there. A check that fails does not stop the
checks after it, so one run shows every problem.

```bash
python3 tools/checks.py                # this machine, in a fresh copy of the working tree
python3 tools/checks.py --linux arm64  # the same inside a Linux container (needs Docker)
python3 tools/checks.py --all          # this machine, Linux arm64 and Linux amd64
```

The copy contains what git would commit. Files that git ignores, such as a
local virtual environment or the corpus files, never reach a check. The copy
is taken once, at the start, and every platform of the run checks the same
copy. Every run states the commit it checked, whether the working tree had
uncommitted changes, and whether the commit contains `origin/main` as last
fetched; `--all` refuses to run while there are uncommitted changes, so that
its result describes exactly what will be merged.

The repository uses no hosted continuous integration. Before a merge,
`python3 tools/checks.py --all` is run on the final commit of the branch, and
its output is put into the pull request.

| Platform | Where it runs |
| --- | --- |
| macOS arm64 | on the development machine |
| Linux arm64 | in a container on the development machine |
| Linux x86_64 | in a container, emulated on the arm64 development machine; nothing runs on real x86_64 hardware |

The containers use the image `python:3.13.5-slim`, pinned by digest, which has
the Python version of `.python-version`. The script names the version of uv
that wrote `uv.lock`; when uv is upgraded, the script is changed with it.

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
- [Changelog](CHANGELOG.md): every change to the interface or to a convention
- [Rules for coding agents](AGENTS.md)

## License

No license has been chosen yet. Until a license file is added to this
repository, all rights are reserved.

# Contributing to pdfacts

pdfacts extracts structured facts from PDF files on two engines, PDFium and
QPDF. Before changing anything, read [ROADMAP.md](ROADMAP.md) for what each
milestone delivers, [docs/architecture.md](docs/architecture.md) for the
layers and the rules, [docs/conventions.md](docs/conventions.md) for the
decisions a reader follows and [docs/provenance.md](docs/provenance.md) for
what may be read and what may not be copied. [AGENTS.md](AGENTS.md) holds
the full rules of this repository, for coding agents and for humans; this
page is the short path for a contribution.

## The workflow

One issue, one branch, one pull request. A branch starts from `main`, and
its pull request says which issue it closes, what was run and where the
change differs from its issue. Pull requests are merged with a merge commit
when all of these hold:

| Condition | Meaning |
| --- | --- |
| checks | the final `python3 tools/checks.py --all` run passed, on the commit that is merged |
| review | a second reviewer found nothing blocking, and the findings are answered in the description |
| description | the pull request states what was run and what it changes |
| changelog | a change to the public interface or to a convention has an entry in [CHANGELOG.md](CHANGELOG.md) |

A change to an existing convention in `docs/conventions.md` needs the
maintainer's consent before the merge.

## The checks

Python 3.13 or newer; dependencies are managed with uv only. A fresh clone
passes the tests: the test suite builds its own PDFs and its own corpora
and needs nothing outside the repository.

```bash
uv sync
uv run pytest
uv run ruff check
uv run ruff format --check
uv run python tools/check_architecture.py
uv build
```

All of these, plus a test of the built wheel, on macOS arm64, Linux arm64
and Linux x86_64, in one run:

```bash
python3 tools/checks.py --all
```

State what was run and what the result was. If a test fails, show the
output; if a step was skipped, say that it was skipped. The corpus the
gates read (`bench/corpus/`) is not part of the repository; its files are
never committed, only listed in `bench/corpus/manifest.csv`, and the gates
place them from a read-only source of the project.

## The rules

The eight rules of [AGENTS.md](AGENTS.md), in short:

- No AGPL code is copied or translated in. Reading MuPDF's and PyMuPDF's
  source to learn from it is allowed; a module written after reading
  third-party source is logged in [docs/provenance.md](docs/provenance.md).
- Expected values in tests come from construction: a test builds its PDF
  with the byte-level builder of the test tree and takes its numbers from
  what it wrote. The output of one library is never the expected value of
  another, and pdfacts is never its own reference.
- Nothing is made up: what cannot be determined is `Unknown` with a reason,
  or an error — never a default that looks like a real value, never an
  empty result in place of a failure.
- The engines stay behind the boundary: pypdfium2 is imported only in
  `src/pdfacts/backends/pdfium.py`, pikepdf only in
  `src/pdfacts/backends/qpdf.py`; `tools/check_architecture.py` checks it.
- Engine versions are pinned exactly; an upgrade is a pull request of its
  own.
- Build what is called: an abstraction arrives when a feature uses it; no
  empty packages, no options for cases that do not exist.
- Nothing private: no names, paths, rules or data of any project that uses
  pdfacts, in code, documents, commit messages, issues or pull requests.
- Report what happened.

## Writing

Documents, comments, commit messages, issues and pull requests are written
in English.

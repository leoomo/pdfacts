# Rules for coding agents

These rules apply to every agent that works in this repository. Humans follow
them too.

## What this project is

pdfacts extracts structured facts from PDF files on top of two engines, PDFium
(through pypdfium2) and QPDF (through pikepdf). Read these before changing
anything:

| Document | Read it for |
| --- | --- |
| [ROADMAP.md](ROADMAP.md) | what each milestone delivers and how it is accepted |
| [docs/architecture.md](docs/architecture.md) | layers, the two engines, the seven rules |
| [docs/conventions.md](docs/conventions.md) | coordinate spaces, the three answers, limits, damaged files |
| [docs/provenance.md](docs/provenance.md) | what may be read and what may not be copied |

If a task contradicts one of these documents, stop and ask. Do not change the
document to fit the task.

## Commands

```bash
uv sync
uv run pytest
uv run ruff check
uv run ruff format --check
uv run python tools/check_architecture.py
uv build
```

All of these, plus a test of the built wheel, in one run and on three
platforms:

```bash
python3 tools/checks.py --all
```

Dependencies are managed with uv only. Python 3.13 or newer.

## Rules

### 1. No copied AGPL code

MuPDF and PyMuPDF are licensed under the AGPL. Reading their source to learn
from it is allowed. Pasting it, or translating it line by line, is not.
Understand the idea, close the source, write the implementation with its own
structure and names. When a module was written after reading third-party
source, add a row to the log in `docs/provenance.md`.

MuPDF bindings are imported under `bench/` only. Never in `src/`, `tests/` or
`tools/`.

### 2. Expected values come from construction

A test builds its PDF with code and takes the expected values from the
construction parameters. The output of one library is never the expected value
for another, and the output of pdfacts is never its own expected value.

Test files whose structure matters are built with the byte-level builder in
the test tree, not with an engine.

### 3. Nothing is made up

When a value cannot be determined, the answer is `Unknown` with a reason, or an
error. Never a default that looks like a real value, never an empty result in
place of a failure. No mock data and no placeholder data in library behaviour.

A PDF built by test code is a test input. It is labelled as synthetic and it
is not a stand-in for a real document in any report.

### 4. Engines stay behind the boundary

pypdfium2 is imported in `src/pdfacts/backends/pdfium.py` only, pikepdf in
`src/pdfacts/backends/qpdf.py` only. Native handles never leave
`src/pdfacts/backends/`. Core does not import feature packages.
`tools/check_architecture.py` enforces the import rules.

### 5. Engine versions are pinned exactly

An engine upgrade is its own change with its own review. Never upgrade an
engine as a side effect.

### 6. Build what is called

An abstraction is added when a feature uses it. No empty packages, no
interfaces without a caller, no options for cases that do not exist yet.

### 7. This repository contains nothing private

No names, paths, rules or data of any project that uses pdfacts. No corpus
files: `bench/corpus/manifest.csv` lists them, the files themselves are never
committed. No secrets. This holds for code, documents, commit messages,
issues and pull requests.

Documents, comments, commit messages, issues and pull requests are written in
English.

### 8. Report what happened

State what was run and what the result was. If a test fails, say so and show
the output. If a step was skipped, say that it was skipped. A gate counts as
passed only when it was run and the result was recorded.

## Workflow

One issue, one branch, one pull request. When a pull request may be merged,
and by whom, is set out at the end of this section.

"The checks" are `tools/checks.py`; there is no hosted continuous
integration, so nothing checks `main` after a merge. Before the final run:

1. `git fetch`, then bring the current `main` into the branch.
2. Commit everything.
3. Run `python3 tools/checks.py --all`.

Put its output into the pull request: the line that names the commit, which
has to say "working tree clean" and "contains origin/main as last fetched",
and the summary of each platform. A result for a commit other than the one
that is merged does not count.

The agent that owns the work decides whether to merge, as a senior architect
would; the maintainer delegated this on 2026-09-28 and can take it back at any
time. A pull request is merged only when all of these hold:

| Condition | Meaning |
| --- | --- |
| checks | the final `--all` run above passed |
| review | a second agent reviewed the pull request and found nothing blocking; what it found is answered in the description |
| description | the pull request states what was run and where it differs from its issue |
| changelog | a change to the public interface or to a convention has an entry in [CHANGELOG.md](CHANGELOG.md) |

These areas get the review's particular attention, because a mistake there is
expensive to undo:

| Area | Examples |
| --- | --- |
| public interface | anything exported from `pdfacts`, fact structures, error types |
| conventions | `docs/conventions.md`, `docs/architecture.md`, `ROADMAP.md` |
| dependency versions | `pyproject.toml`, `uv.lock`; an engine upgrade is a pull request of its own |
| architecture check | `tools/check_architecture.py` and its tests |

One kind of change still needs the maintainer's consent before the merge: a
change to an existing convention in `docs/conventions.md` (decision of
2026-09-27, stated at the top of that document). Ask before merging, and
record the answer in the pull request. Adding a convention that an issue
already specifies is not such a change.

Merge with a merge commit, not a squash. Tell the maintainer after each merge.

Until version 1.0 the interface may change in ways that break callers. Every
such change, and every change to a convention, gets an entry in
[CHANGELOG.md](CHANGELOG.md).

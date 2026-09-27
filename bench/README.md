# bench

Tooling for measuring pdfacts against other libraries. Nothing in this folder
is part of the wheel.

## Corpus

`corpus/manifest.csv` names 104 official German forms by size and SHA-256. The
files are fetched from their sources and are never committed.

```bash
uv run python bench/corpus/fetch.py                 # verify what is present, fetch the rest
uv run python bench/corpus/fetch.py --verify-only   # no network access
uv run python bench/corpus/fetch.py --from DIR      # copy matching files from a local folder
uv run python bench/corpus/fetch.py --check-sources # download every verified source and compare; writes nothing
```

The manifest states for every entry what is known about its source:

| `source_state` | Meaning | Entries on 2026-09-27 |
| --- | --- | --- |
| `verified` | the source served exactly the listed bytes on the date in `source_checked` | 98 |
| `changed` | the publisher serves other bytes now | 5 |
| `local-only` | no published file has these bytes | 1 |

Only a verified source is downloaded. The six other entries can only be
supplied with `--from`.

A form that was revised by its publisher is reported as `changed` and kept
next to the expected name with the suffix `.changed`. It is never accepted in
place of the listed file. When the revised form is wanted in the corpus, it is
added as a new entry; the old entry stays.

Requests to one host are spaced by three seconds. A host that answers with
status 429 is asked again once, after twenty seconds, and at that pace from
then on. The script identifies itself in the `User-Agent` header, which some
publishers require of automated clients.

## Referees

A comparison needs a referee that is independent of the libraries being
compared. In order of trust:

1. **Construction.** The test file is built by code; the expected coordinates
   are the construction parameters.
2. **File content.** Options, values and flags of form fields, read from the
   dictionaries in the file.
3. **Rendering.** The page is rendered and boxes are checked against the ink.
4. **Agreement plus review.** Where independent engines agree, the result is
   trusted. Disagreements are judged by a person and recorded.

The output of one library is never the expected value for another.

## Gate scripts and the digest tool

Not written yet. They arrive with milestone M1: one script per gate, and a
digest of the facts of a file that is used to compare platforms and to see
what an engine upgrade changes.

## Comparison with other libraries

Deferred to milestone M7.

PyMuPDF may be installed here for comparison. It is licensed under the AGPL
and must not be imported by the library or by the library's tests.

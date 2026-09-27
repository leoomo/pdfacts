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
```

13 entries have no source address yet. They can only be supplied with `--from`.

A form that was revised by its publisher is reported as `changed` and kept
next to the expected name with the suffix `.changed`. It is never accepted in
place of the listed file.

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

## Comparison tooling

Not written yet. It follows once pdfacts produces facts worth comparing
(milestone M1).

PyMuPDF may be installed here for comparison. It is licensed under the AGPL
and must not be imported by the library or by the library's tests.

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

### How the script behaves towards the hosts

Some publishers state conditions for automated clients. The script keeps them
for every host:

| Behaviour | Detail |
| --- | --- |
| it names itself | the `User-Agent` header starts with `pdfacts-corpus-fetch` |
| it reads `robots.txt` first | an address that the host disallows is not fetched |
| `robots.txt` cannot be read | nothing is fetched from that host; a missing file (status 404) means the host has no rules |
| spacing | three seconds between two requests to one host, or the crawl delay the host states, whichever is longer |
| status 429 | it waits 20 seconds and tries again, then 60 seconds, then reports the failure; a longer wait asked for by the host is kept, up to 120 seconds; the slower pace stays for the rest of the run |

A run over all sources sends 132 requests to 34 hosts. Do not repeat it
several times a day: one host answered with status 429 after three such runs
on 2026-09-27.

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

The M1 gate reads every file of the corpus twice: page geometry through
`pdfacts.open`, and glyph data through PDFium itself, after the runtime
installed the font policy. What is there:

| Script | What it does |
| --- | --- |
| `digest.py` | SHA-256 over int, float and str values; the module states the encoding |
| `gates/m1.py` | reads the corpus and writes one receipt: per file a geometry digest, a glyph digest, counts, warnings |
| `gates/compare.py` | compares two or more receipts, prints one table per file and column: identical, differs, missing, error |
| `gates/m2.py`, `gates/m2_referee.py` | the M2 gate: the widgets and fields of the corpus, against a referee that reads the dictionaries with pikepdf |
| `gates/m3.py` | the M3 gate: the paths and images of the corpus, read twice, their numbers against PDFium's own |
| `gates/m4a.py` | the M4a gate: the glyphs, words and lines of the corpus, digested for comparing platforms, their characters against PDFium's own count, and what the rule set leaves out or may get wrong, counted |

The M2, M3 and M4a gates take the same `--commit`, `--out`, `--only` and
`--corpus` as the M1 gate and refuse a run on the same grounds; the font
policy is checked by the M4a gate as well, not by the M2 and M3 gates, which
run on one machine. The rest of this section is about the M1 gate.

Run the gate on one machine, from the repository root, with the corpus in
place (`bench/corpus/fetch.py --verify-only` reports `104 files: ok 104`):

```bash
uv run python bench/gates/m1.py --commit "$(git rev-parse HEAD)" --out bench/out/m1-manual.json
```

`--commit` takes the commit of the working tree, 40 lowercase hex digits.
`--only ID ...` reads part of the corpus and writes `"partial": true` into
the receipt; `--corpus DIR` reads the files from another folder. A file that
is missing, or whose SHA-256 is not the one in the manifest, ends the run
with status 2 and no receipt. Status 1 means the receipt was written and a
file is under `errors`.

Compare receipts with:

```bash
uv run python bench/gates/compare.py bench/out/m1-darwin-arm64.json bench/out/m1-linux-arm64.json
```

It refuses a receipt that is partial, that names other engines or another
commit, or where no receipt names any file. It exits 0 only when every file
is identical in every column.

Receipts go to `bench/out/`, which git ignores; they are never committed. A
receipt holds digests, counts and versions, never text of a document and
never a warning text.

### The run on three platforms

```bash
python3 bench/gates/run.py
```

`run.py` runs the gate on macOS natively (in a fresh copy of the working
tree) and in the Linux arm64 and Linux x86_64 containers of `tools/checks.py`,
all three over the same snapshot of the tree. It refuses to run while the
working tree has uncommitted changes, and when
`bench/corpus/fetch.py --verify-only` does not report `104 files: ok 104`.
The corpus is not part of the snapshot: each run places it from a read-only
source with `fetch.py --from`, which checks every hash. A platform that fails
leaves no receipt of an earlier run behind.

The three receipts go to `bench/out/`, named `m1-darwin-arm64.json`,
`m1-linux-arm64.json` and `m1-linux-amd64.json` on this machine. A full run
took 14.5 s on the host, 25.0 s in the arm64 container and 32.3 s in the
emulated amd64 container (2026-09-29); reading the corpus alone took 13.1 s,
19.0 s and 25.0 s. Compare them with:

```bash
uv run python bench/gates/compare.py bench/out/m1-darwin-arm64.json bench/out/m1-linux-arm64.json bench/out/m1-linux-amd64.json
```

`run.py` does not compare the receipts itself; that is the next step, and its
exit status says whether every file was identical on every platform.

## Comparison with other libraries

Deferred to milestone M7.

PyMuPDF may be installed here for comparison. It is licensed under the AGPL
and must not be imported by the library or by the library's tests.

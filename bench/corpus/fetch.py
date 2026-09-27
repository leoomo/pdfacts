"""Fetch the corpus files named in manifest.csv and verify them.

Files are written to bench/corpus/files/, which is not committed. Every file
ends in exactly one state:

  ok             present, SHA-256 equals the manifest
  changed        the source now serves different bytes; kept as NAME.changed
  no-source      the manifest has no URL for it and it is not present locally
  failed         the download failed

Official forms are revised regularly, so `changed` is expected over time. It
is reported, never silently accepted.

Usage:
  python bench/corpus/fetch.py                  verify what is present, fetch the rest
  python bench/corpus/fetch.py --verify-only    no network access
  python bench/corpus/fetch.py --from DIR       copy matching files from a local folder
  python bench/corpus/fetch.py --only ID ...    restrict to some manifest ids
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import shutil
import sys
import urllib.error
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "manifest.csv"
FILES = HERE / "files"
USER_AGENT = "pdfacts-corpus-fetch/0.1"
TIMEOUT_SECONDS = 60
MAX_BYTES = 50 * 1024 * 1024


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def download(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        data = response.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError(f"larger than {MAX_BYTES} bytes")
    if not data.startswith(b"%PDF-"):
        raise ValueError("response is not a PDF")
    return data


def process(row: dict[str, str], *, local: Path | None, network: bool) -> tuple[str, str]:
    name = row["file"]
    target = FILES / name
    expected = row["sha256"]

    if target.exists():
        return ("ok", "") if sha256(target) == expected else ("changed", "local file differs")

    if local is not None:
        candidate = local / name
        if candidate.is_file() and sha256(candidate) == expected:
            shutil.copyfile(candidate, target)
            return "ok", "copied from local folder"

    url = row["source_url"].strip()
    if not url:
        return "no-source", "manifest has no URL"
    if not network:
        return "failed", "not present and network access is disabled"
    try:
        data = download(url)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as error:
        return "failed", f"{type(error).__name__}: {error}"
    if hashlib.sha256(data).hexdigest() != expected:
        target.with_name(name + ".changed").write_bytes(data)
        return "changed", "source serves different bytes"
    target.write_bytes(data)
    return "ok", "downloaded"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--from", dest="local", type=Path)
    parser.add_argument("--only", nargs="*", default=[])
    args = parser.parse_args()

    with MANIFEST.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if args.only:
        rows = [row for row in rows if row["id"] in set(args.only)]
    FILES.mkdir(exist_ok=True)

    counts: dict[str, int] = {}
    for row in rows:
        state, note = process(row, local=args.local, network=not args.verify_only)
        counts[state] = counts.get(state, 0) + 1
        if state != "ok" or note:
            print(f"{state:10} {row['id']:>4} {row['file']}  {note}")
    summary = ", ".join(f"{state} {count}" for state, count in sorted(counts.items()))
    print(f"{len(rows)} files: {summary}")
    return 0 if set(counts) <= {"ok"} else 1


if __name__ == "__main__":
    sys.exit(main())

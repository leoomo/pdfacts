"""Fetch the corpus files named in manifest.csv and verify them.

Files are written to bench/corpus/files/, which is not committed. Every file
ends in exactly one state:

  ok             present, SHA-256 equals the manifest
  changed        the source now serves different bytes; kept as NAME.changed
  no-source      the manifest has no verified source for it and it is not
                 present locally
  failed         the download failed

The manifest records for every entry what is known about its source:

  verified       the source served exactly the listed bytes on the date given
  changed        the publisher serves other bytes now; the listed file can
                 only be supplied with --from
  local-only     no published file has these bytes

Official forms are revised regularly, so `changed` is expected over time. It
is reported, never silently accepted.

Usage:
  python bench/corpus/fetch.py                  verify what is present, fetch the rest
  python bench/corpus/fetch.py --verify-only    no network access
  python bench/corpus/fetch.py --from DIR       copy matching files from a local folder
  python bench/corpus/fetch.py --only ID ...    restrict to some manifest ids
  python bench/corpus/fetch.py --check-sources  download every verified source and
                                                compare it; writes no file
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import shutil
import sys
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from pathlib import Path
from urllib.parse import urlparse

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "manifest.csv"
FILES = HERE / "files"
# Several publishers ask automated clients to identify themselves.
USER_AGENT = "pdfacts-corpus-fetch/0.2 (checks published forms against recorded hashes)"
TIMEOUT_SECONDS = 90
MAX_BYTES = 50 * 1024 * 1024
HOST_PAUSE_SECONDS = 3.0
HOST_PAUSE_AFTER_LIMIT_SECONDS = 20.0
HTTP_TOO_MANY_REQUESTS = 429

SOURCE_STATES = ("verified", "changed", "local-only")


class Pacer:
    """Keeps a minimum interval between two requests to the same host."""

    def __init__(
        self,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._clock = clock
        self._sleep = sleep
        self._last: dict[str, float] = {}
        self._pause: dict[str, float] = {}

    def wait(self, url: str) -> None:
        host = urlparse(url).netloc
        last = self._last.get(host)
        if last is not None:
            remaining = last + self._pause.get(host, HOST_PAUSE_SECONDS) - self._clock()
            if remaining > 0:
                self._sleep(remaining)
        self._last[host] = self._clock()

    def slow_down(self, url: str) -> None:
        self._pause[urlparse(url).netloc] = HOST_PAUSE_AFTER_LIMIT_SECONDS


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def verified_source(row: dict[str, str]) -> str:
    """The address to download from, or "" when the manifest has no verified one."""
    url = (row.get("source_url") or "").strip()
    state = (row.get("source_state") or "verified").strip()
    return url if state == "verified" else ""


def _request(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
        return response.read(MAX_BYTES + 1)


def download(url: str, pacer: Pacer, request: Callable[[str], bytes] = _request) -> bytes:
    pacer.wait(url)
    try:
        data = request(url)
    except urllib.error.HTTPError as error:
        if error.code != HTTP_TOO_MANY_REQUESTS:
            raise
        # The host limits the rate: slow down for this host and try once more.
        pacer.slow_down(url)
        pacer.wait(url)
        data = request(url)
    if len(data) > MAX_BYTES:
        raise ValueError(f"larger than {MAX_BYTES} bytes")
    if not data.startswith(b"%PDF-"):
        raise ValueError("response is not a PDF")
    return data


def process(
    row: dict[str, str],
    *,
    files: Path,
    local: Path | None,
    network: bool,
    pacer: Pacer,
    request: Callable[[str], bytes] = _request,
) -> tuple[str, str]:
    name = row["file"]
    target = files / name
    expected = row["sha256"]

    if target.exists():
        return ("ok", "") if sha256(target) == expected else ("changed", "local file differs")

    if local is not None:
        candidate = local / name
        if candidate.is_file() and sha256(candidate) == expected:
            shutil.copyfile(candidate, target)
            return "ok", "copied from local folder"

    url = verified_source(row)
    if not url:
        state = (row.get("source_state") or "").strip() or "none"
        return "no-source", f"manifest has no verified source (source state: {state})"
    if not network:
        return "failed", "not present and network access is disabled"
    try:
        data = download(url, pacer, request)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as error:
        return "failed", f"{type(error).__name__}: {error}"
    if hashlib.sha256(data).hexdigest() != expected:
        target.with_name(name + ".changed").write_bytes(data)
        return "changed", "source serves different bytes"
    target.write_bytes(data)
    return "ok", "downloaded"


def check_source(
    row: dict[str, str],
    *,
    pacer: Pacer,
    request: Callable[[str], bytes] = _request,
) -> tuple[str, str]:
    """Download the source and compare it with the manifest. Writes nothing."""
    url = verified_source(row)
    if not url:
        return "skipped", "no verified source"
    try:
        data = download(url, pacer, request)
    except (urllib.error.URLError, TimeoutError, ValueError, OSError) as error:
        return "failed", f"{type(error).__name__}: {error}"
    if hashlib.sha256(data).hexdigest() != row["sha256"]:
        return "changed", f"source serves {len(data)} bytes, manifest lists {row['bytes']}"
    return "ok", ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--verify-only", action="store_true")
    parser.add_argument("--check-sources", action="store_true")
    parser.add_argument("--from", dest="local", type=Path)
    parser.add_argument("--only", nargs="*", default=[])
    args = parser.parse_args()

    with MANIFEST.open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if args.only:
        rows = [row for row in rows if row["id"] in set(args.only)]
    pacer = Pacer()

    counts: dict[str, int] = {}
    if args.check_sources:
        for row in rows:
            state, note = check_source(row, pacer=pacer)
            counts[state] = counts.get(state, 0) + 1
            if state not in ("ok", "skipped"):
                print(f"{state:10} {row['id']:>4} {row['file']}  {note}")
        summary = ", ".join(f"{state} {count}" for state, count in sorted(counts.items()))
        print(f"{len(rows)} sources: {summary}")
        return 0 if set(counts) <= {"ok", "skipped"} else 1

    FILES.mkdir(exist_ok=True)
    for row in rows:
        state, note = process(
            row, files=FILES, local=args.local, network=not args.verify_only, pacer=pacer
        )
        counts[state] = counts.get(state, 0) + 1
        if state != "ok" or note:
            print(f"{state:10} {row['id']:>4} {row['file']}  {note}")
    summary = ", ".join(f"{state} {count}" for state, count in sorted(counts.items()))
    print(f"{len(rows)} files: {summary}")
    return 0 if set(counts) <= {"ok"} else 1


if __name__ == "__main__":
    sys.exit(main())

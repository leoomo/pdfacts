"""A folder of files and a manifest for a gate to read, and a run of the gate over them."""

from __future__ import annotations

import csv
import hashlib
from pathlib import Path


def corpus(gate, tmp_path, monkeypatch, files: dict[str, bytes], listed=None) -> Path:
    """`files` in a folder, and a manifest that lists them, ids 01, 02, ..., which the module
    `gate` reads; `listed` gives other bytes to hash for a file."""
    folder = tmp_path / "corpus"
    folder.mkdir()
    manifest = tmp_path / "manifest.csv"
    with manifest.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["id", "file", "sha256"])
        for number, (name, data) in enumerate(files.items(), 1):
            (folder / name).write_bytes(data)
            hashed = (listed or {}).get(name, data)
            writer.writerow([f"{number:02d}", name, hashlib.sha256(hashed).hexdigest()])
    monkeypatch.setattr(gate, "MANIFEST", manifest)
    return folder


def run(gate, folder: Path, out: Path, *more: str) -> int:
    """The gate's main with a commit of zeros, the receipt at `out`, the files in `folder`."""
    return gate.main(["--commit", "0" * 40, "--out", str(out), "--corpus", str(folder), *more])

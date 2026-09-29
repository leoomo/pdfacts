"""Compare gate receipts: what each platform read, and whether it agrees.

Takes two or more receipts written by m1.py, refuses those that would not
answer the question (partial, other engines, another commit, no file), prints
the numbers of each receipt and one table over the files of all receipts
together, and says with its exit status whether every cell is identical.
Standard library only.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence

FILE_KEYS = (
    "page_count",
    "glyph_count",
    "geometry",
    "glyphs",
    "glyphs_rounded3",
    "warning_count",
    "cross_check_failed",
)
# The digest key in a receipt, and the column it fills in the table.
COLUMNS = (("geometry", "geometry"), ("glyphs", "glyphs"), ("glyphs_rounded3", "glyphs, rounded"))


def refuse(path: str, reason: str) -> int:
    print(f"{path}: {reason}", file=sys.stderr)
    return 2


def read_receipt(path: str) -> dict | None:
    """One receipt as a dictionary, or None with the refusal already printed."""
    try:
        with open(path, encoding="utf-8") as handle:
            receipt = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        refuse(path, f"cannot be read: {error}")
        return None
    top_level = {"environment", "partial", "files", "errors"}
    if not isinstance(receipt, dict) or not top_level <= set(receipt):
        refuse(path, "not a receipt: environment, partial, files or errors is missing")
        return None
    environment = receipt["environment"]
    if (
        not isinstance(environment, dict)
        or not isinstance(environment.get("engine_info"), dict)
        or "commit" not in environment
    ):
        refuse(path, "not a receipt: environment without engine_info or commit")
        return None
    for name, source in (("files", receipt["files"]), ("errors", receipt["errors"])):
        if not isinstance(source, dict):
            refuse(path, f"not a receipt: {name} is not a dictionary")
            return None
    for name, entry in receipt["files"].items():
        if not isinstance(entry, dict) or not set(FILE_KEYS) <= set(entry):
            refuse(path, f"not a receipt: the entry of {name} lacks one of the file keys")
            return None
    return receipt


def print_block(path: str, receipt: dict) -> None:
    environment = receipt["environment"]
    files = receipt["files"]
    print(f"receipt {path}")
    print(
        f"  {environment['platform']}, machine {environment['machine']}, "
        f"Python {environment['python']}, commit {environment['commit']}, {environment['date']}"
    )
    print(
        f"  {len(files)} files processed, {len(receipt['errors'])} errors, "
        f"{sum(entry['page_count'] for entry in files.values())} pages, "
        f"{sum(entry['glyph_count'] for entry in files.values())} glyphs, "
        f"{sum(entry['warning_count'] for entry in files.values())} warnings, "
        f"{receipt['seconds']} s"
    )
    failed = [
        f"{name}: " + ", ".join(str(index) for index in entry["cross_check_failed"])
        for name, entry in sorted(files.items())
        if entry["cross_check_failed"]
    ]
    print("  pages whose cross-check failed: " + ("; ".join(failed) or "none"))
    errors = [f"{name}: {kind}" for name, kind in sorted(receipt["errors"].items())]
    print("  errors: " + ("; ".join(errors) or "none"))
    print()


def main(argv: Sequence[str] | None = None) -> int:
    paths = list(sys.argv[1:] if argv is None else argv)
    if len(paths) < 2:
        print(f"a comparison takes at least two receipts, {len(paths)} given", file=sys.stderr)
        return 2
    receipts: list[tuple[str, dict]] = []
    for path in paths:
        receipt = read_receipt(path)
        if receipt is None:
            return 2
        receipts.append((path, receipt))
    for path, receipt in receipts:
        if receipt["partial"] is True:
            return refuse(path, "is partial")
    engines = receipts[0][1]["environment"]["engine_info"]
    for path, receipt in receipts[1:]:
        other = receipt["environment"]["engine_info"]
        differing = sorted(
            key for key in engines.keys() | other.keys() if engines.get(key) != other.get(key)
        )
        if differing:
            return refuse(path, "engine_info differs in " + ", ".join(differing))
    commit = receipts[0][1]["environment"]["commit"]
    for path, receipt in receipts[1:]:
        other_commit = receipt["environment"]["commit"]
        if other_commit != commit:
            return refuse(path, f"names commit {other_commit}, {paths[0]} names {commit}")
    names = sorted(
        {name for _, receipt in receipts for name in (*receipt["files"], *receipt["errors"])}
    )
    if not names:
        print("no receipt names a file under files or errors", file=sys.stderr)
        return 2
    for path, receipt in receipts:
        print_block(path, receipt)
    print("| file | geometry | glyphs | glyphs, rounded |")
    print("| --- | --- | --- | --- |")
    identical = {label: 0 for _, label in COLUMNS}
    for name in names:
        if any(name in receipt["errors"] for _, receipt in receipts):
            cells = ["error"] * len(COLUMNS)
        elif any(name not in receipt["files"] for _, receipt in receipts):
            cells = ["missing"] * len(COLUMNS)
        else:
            cells = []
            for key, _ in COLUMNS:
                digests = {receipt["files"][name][key] for _, receipt in receipts}
                cells.append("identical" if len(digests) == 1 else "differs")
        print("| " + " | ".join([name, *cells]) + " |")
        for (_, label), cell in zip(COLUMNS, cells, strict=True):
            if cell == "identical":
                identical[label] += 1
    print()
    every_cell_identical = True
    for _, label in COLUMNS:
        print(f"{label}: identical in {identical[label]} of {len(names)} files")
        if identical[label] != len(names):
            every_cell_identical = False
    return 0 if every_cell_identical else 1


if __name__ == "__main__":
    raise SystemExit(main())

"""Compare gate receipts: what each platform read, and whether it agrees.

Takes two or more receipts that one gate wrote (m1.py or m4a.py, named in
the receipt), refuses those that would not answer the question (partial,
another gate, other engines, another commit, no file), prints the numbers of
each receipt and one table over the files of all receipts together, and says
with its exit status whether every cell is identical. Standard library only.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from dataclasses import dataclass


@dataclass(frozen=True)
class Gate:
    """What a gate's receipt holds for each file, and what is compared."""

    # The keys of an int, summed in a receipt's numbers, with their words.
    counts: tuple[tuple[str, str], ...]
    # The keys of a digest, a string.
    digests: tuple[str, ...]
    # The columns of the table: a file's cell is identical when every key of
    # its column holds the same value in every receipt.
    columns: tuple[tuple[str, tuple[str, ...]], ...]
    # The key of a list of the pages that failed a check, the type of its
    # items, and the words a receipt's numbers give it.
    failed: tuple[str, type, str]
    # The keys of a dictionary of counts, each an int.
    tallies: tuple[str, ...] = ()


GATES = {
    "m1": Gate(
        counts=(("page_count", "pages"), ("glyph_count", "glyphs"), ("warning_count", "warnings")),
        digests=("geometry", "glyphs"),
        columns=(("geometry", ("geometry",)), ("glyphs", ("glyphs",))),
        failed=("cross_check_failed", int, "pages whose cross-check failed"),
    ),
    "m4a": Gate(
        counts=(
            ("page_count", "pages"),
            ("glyph_count", "glyphs"),
            ("word_count", "words"),
            ("line_count", "lines"),
            ("pages_not_compared", "pages not compared"),
        ),
        digests=("glyphs", "words", "lines"),
        columns=(
            ("glyphs", ("glyphs",)),
            ("words", ("words",)),
            ("lines", ("lines",)),
            (
                "tallies",
                ("unknown", "known", "breaks", "pdfium_differences", "pages_not_compared"),
            ),
        ),
        failed=("pdfium_differences", str, "pages whose characters differ from PDFium's"),
        tallies=("unknown", "known", "breaks"),
    ),
}


def refuse(path: str, reason: str) -> int:
    print(f"{path}: {reason}", file=sys.stderr)
    return 2


def file_keys(gate: Gate) -> set[str]:
    keys = {key for key, _ in gate.counts} | set(gate.digests) | {gate.failed[0]}
    return keys | set(gate.tallies) | {key for _, compared in gate.columns for key in compared}


def read_receipt(path: str) -> dict | None:
    """One receipt as a dictionary, or None with the refusal already printed."""
    try:
        with open(path, encoding="utf-8") as handle:
            receipt = json.load(handle)
    except (OSError, json.JSONDecodeError) as error:
        refuse(path, f"cannot be read: {error}")
        return None
    top_level = {"gate", "environment", "partial", "files", "errors"}
    if not isinstance(receipt, dict) or not top_level <= set(receipt):
        refuse(path, "not a receipt: gate, environment, partial, files or errors is missing")
        return None
    if not isinstance(receipt["gate"], str) or receipt["gate"] not in GATES:
        refuse(path, f"not a receipt: no gate is called {receipt['gate']!r}")
        return None
    gate = GATES[receipt["gate"]]
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
        if not isinstance(entry, dict) or not file_keys(gate) <= set(entry):
            refuse(path, f"not a receipt: the entry of {name} lacks one of the file keys")
            return None
    if not read_report(path, receipt, gate):
        return None
    return receipt


def read_report(path: str, receipt: dict, gate: Gate) -> bool:
    """Refuse what the report reads but cannot trust; True when all of it is there.

    A key that is missing, or a value of another type, would crash the report
    with status 1; it is refused with 2 instead, the key named.
    """
    environment = receipt["environment"]
    missing = [key for key in ("platform", "machine", "python", "date") if key not in environment]
    if missing:
        refuse(path, "not a receipt: environment without " + ", ".join(missing))
        return False
    seconds = receipt.get("seconds")
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)):
        refuse(path, "not a receipt: seconds is not a number")
        return False
    key, kind, _ = gate.failed
    for name, entry in receipt["files"].items():
        for count, _ in gate.counts:
            if type(entry[count]) is not int:
                refuse(path, f"not a receipt: {name}: {count} is not an int")
                return False
        failed = entry[key]
        if not isinstance(failed, list) or any(type(item) is not kind for item in failed):
            refuse(path, f"not a receipt: {name}: {key} is not a list of {kind.__name__}s")
            return False
        for tally in gate.tallies:
            counted = entry[tally]
            if not isinstance(counted, dict) or any(type(n) is not int for n in counted.values()):
                refuse(path, f"not a receipt: {name}: {tally} is not a dictionary of ints")
                return False
        for digest in gate.digests:
            if not isinstance(entry[digest], str):
                refuse(path, f"not a receipt: {name}: {digest} is not a string")
                return False
    for name, error in receipt["errors"].items():
        if not isinstance(error, str):
            refuse(path, f"not a receipt: the error of {name} is not a string")
            return False
    return True


def print_block(path: str, receipt: dict, gate: Gate) -> None:
    environment = receipt["environment"]
    files = receipt["files"]
    print(f"receipt {path}")
    print(
        f"  {environment['platform']}, machine {environment['machine']}, "
        f"Python {environment['python']}, commit {environment['commit']}, {environment['date']}"
    )
    counted = "".join(
        f"{sum(entry[key] for entry in files.values())} {words}, " for key, words in gate.counts
    )
    print(
        f"  {len(files)} files processed, {len(receipt['errors'])} errors, "
        f"{counted}{receipt['seconds']} s"
    )
    key, _, words = gate.failed
    failed = [
        f"{name}: " + ", ".join(str(index) for index in entry[key])
        for name, entry in sorted(files.items())
        if entry[key]
    ]
    print(f"  {words}: " + ("; ".join(failed) or "none"))
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
        if receipt["partial"] is not False:
            return refuse(path, "partial is not false")
    name = receipts[0][1]["gate"]
    for path, receipt in receipts[1:]:
        if receipt["gate"] != name:
            return refuse(path, f"is a receipt of the gate {receipt['gate']}, {paths[0]} of {name}")
    gate = GATES[name]
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
        {file for _, receipt in receipts for file in (*receipt["files"], *receipt["errors"])}
    )
    if not names:
        print("no receipt names a file under files or errors", file=sys.stderr)
        return 2
    for path, receipt in receipts:
        print_block(path, receipt, gate)
    header = ["file", *(label for label, _ in gate.columns)]
    print("| " + " | ".join(header) + " |")
    print("| " + " | ".join(["---"] * len(header)) + " |")
    identical = {label: 0 for label, _ in gate.columns}
    for file in names:
        if any(file in receipt["errors"] for _, receipt in receipts):
            cells = ["error"] * len(gate.columns)
        elif any(file not in receipt["files"] for _, receipt in receipts):
            cells = ["missing"] * len(gate.columns)
        else:
            cells = []
            for _, compared in gate.columns:
                values = {
                    json.dumps([receipt["files"][file][key] for key in compared], sort_keys=True)
                    for _, receipt in receipts
                }
                cells.append("identical" if len(values) == 1 else "differs")
        print("| " + " | ".join([file, *cells]) + " |")
        for (label, _), cell in zip(gate.columns, cells, strict=True):
            if cell == "identical":
                identical[label] += 1
    print()
    every_cell_identical = True
    for label, _ in gate.columns:
        print(f"{label}: identical in {identical[label]} of {len(names)} files")
        if identical[label] != len(names):
            every_cell_identical = False
    return 0 if every_cell_identical else 1


if __name__ == "__main__":
    raise SystemExit(main())

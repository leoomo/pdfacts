"""The M2 gate: the widgets and fields of the corpus, pdfacts against the referee.

pdfacts.widgets and pdfacts.fields are read twice and compared with
m2_referee.read, a reading of the dictionaries with pikepdf that is
independent of pdfacts (#40). The receipt holds no text of a document: a
difference is named by page, index and fact, never by its values. Run from
the repository root:

    uv run python bench/gates/m2.py --commit SHA --out PATH [--only ID ...] [--corpus DIR]

Exit status 0 when every file has no differences and no field differences,
is stable, has fields_equal true and no error; 1 otherwise; 2 when the run
was refused and no receipt was written.
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import platform
import re
import sys
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import m2_referee

import pdfacts
from pdfacts import Unknown
from pdfacts.core import runtime
from pdfacts.core.engines import engine_info
from pdfacts.core.geometry import Rect
from pdfacts.forms.model import BorderStyle, ChoiceOption, DeviceColour, FieldType

ROOT = Path(__file__).resolve().parents[2]

# What the receipt holds of each file (decision 4); the printing of decision 5
# reads what check_file also returns beyond these.
RECEIPT_FILE_KEYS = (
    "widgets",
    "differences",
    "unknown",
    "stable",
    "fields",
    "fields_equal",
    "field_differences",
)


def commit_argument(value: str) -> str:
    if re.fullmatch(r"[0-9a-f]{40}", value) is None:
        raise argparse.ArgumentTypeError(f"40 lowercase hex digits expected, not {value!r}")
    return value


def _unknown_form(value: Unknown) -> dict:
    return {"unknown": value.reason.value}


def _plain(value):
    """One fact of pdfacts in the referee's plain form."""
    if isinstance(value, Unknown):
        return _unknown_form(value)
    if value is None:
        return None
    if isinstance(value, FieldType):
        return value.value
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return value + 0.0
    if isinstance(value, str):
        return value
    if isinstance(value, tuple):
        return [_plain(one) for one in value]
    if isinstance(value, Rect):
        return [value.x0 + 0.0, value.y0 + 0.0, value.x1 + 0.0, value.y1 + 0.0]
    if hasattr(value, "raw") and hasattr(value, "text"):
        return {"name": value.raw.hex()}
    if isinstance(value, DeviceColour):
        return [one + 0.0 for one in value.components]
    if isinstance(value, BorderStyle):
        return [
            value.width + 0.0 if value.width is not None else None,
            {"name": value.style.raw.hex()} if value.style is not None else None,
        ]
    if isinstance(value, ChoiceOption):
        return [value.export, value.display]
    raise TypeError(f"a fact of an unknown kind: {type(value).__name__}")


def as_plain(widget, page_index: int) -> dict:
    """A pdfacts Widget in decision 2's form."""
    out = {"page": page_index, "index": widget.ref.annotation_index}
    out["object"] = (
        _unknown_form(widget.ref.object_number)
        if isinstance(widget.ref.object_number, Unknown)
        else (
            [widget.ref.object_number, widget.ref.generation]
            if widget.ref.object_number is not None
            else None
        )
    )
    out["rect"] = _plain(widget.rect)
    out["annotation_flags"] = _plain(widget.annotation_flags)
    field = widget.field
    for fact in m2_referee.FACTS[3:13]:
        if isinstance(field, Unknown):
            out[fact] = _unknown_form(field)
        elif fact == "field_ref":
            number = field.ref.object_number
            out[fact] = (
                _unknown_form(number)
                if isinstance(number, Unknown)
                else [number, field.ref.generation]
                if number is not None
                else None
            )
        else:
            out[fact] = _plain(getattr(field, fact))
    for fact in m2_referee.FACTS[13:]:
        out[fact] = _plain(getattr(widget, fact))
    return out


def differences(ours: list[dict], theirs: list[dict]) -> list[list]:
    """Per widget the facts that differ; a widget one side alone has, by name."""
    out: list[list] = []
    theirs_left = {(one["page"], one["index"]): one for one in theirs}
    for one in ours:
        other = theirs_left.pop((one["page"], one["index"]), None)
        if other is None:
            out.append([one["page"], one["index"], "widget"])
            continue
        out.extend(
            [one["page"], one["index"], fact]
            for fact in m2_referee.FACTS
            if one[fact] != other[fact]
        )
    for other in theirs_left.values():
        out.append([other["page"], other["index"], "widget"])
    return out


def check_file(data: bytes) -> dict:
    """One file through pdfacts (twice) and the referee, in one entry.

    The entry holds decision 4's keys and, for the printing of decision 5,
    the counts of the types and of the known and equal facts.
    """
    with pdfacts.open(data) as document:
        pages = [pdfacts.widgets(document, index) for index in range(document.page_count)]
        fields = pdfacts.fields(document)
        stable = pages == [
            pdfacts.widgets(document, index) for index in range(document.page_count)
        ] and fields == pdfacts.fields(document)
    ours = [
        as_plain(widget, page_index)
        for page_index, widgets in enumerate(pages)
        for widget in widgets
    ]
    theirs, their_fields = m2_referee.read(data)
    entry: dict = {}
    entry["widgets"] = len(ours)
    entry["differences"] = differences(ours, theirs)
    entry["stable"] = stable
    unknown: dict[str, int] = {}
    types: dict[str, int] = {}
    known: dict[str, int] = {fact: 0 for fact in m2_referee.FACTS}
    equal: dict[str, int] = {fact: 0 for fact in m2_referee.FACTS}
    theirs_by_key = {(one["page"], one["index"]): one for one in theirs}
    in_form_true = 0
    unknown_types: dict[str, dict[str, int]] = {}
    equal_of_buttons = {"states": 0, "state": 0, "export_value": 0}
    equal_of_choice = {"options": 0}
    for one in ours:
        kind = one["type"]
        kind = kind if isinstance(kind, str) else "unknown"
        types[kind] = types.get(kind, 0) + 1
        for fact in m2_referee.FACTS:
            value = one[fact]
            if isinstance(value, dict) and "unknown" in value:
                key = f"{fact} {value['unknown']}"
                unknown[key] = unknown.get(key, 0) + 1
                by_type = unknown_types.setdefault(key, {})
                by_type[kind] = by_type.get(kind, 0) + 1
            elif value is not None:
                known[fact] += 1
            if fact == "in_form" and value is True:
                in_form_true += 1
        other = theirs_by_key.get((one["page"], one["index"]))
        if other is not None:
            for fact in m2_referee.FACTS:
                if one[fact] == other[fact]:
                    equal[fact] += 1
            # The buttons carry the states and export values, the choice fields the options.
            if kind in ("checkbox", "radio"):
                for fact in equal_of_buttons:
                    if one[fact] == other[fact]:
                        equal_of_buttons[fact] += 1
            if kind in ("combo-box", "list-box") and one["options"] == other["options"]:
                equal_of_choice["options"] += 1
    entry["unknown"] = unknown
    entry["unknown_types"] = unknown_types
    entry["types"] = types
    entry["known"] = known
    entry["equal"] = equal
    entry["equal_of_buttons"] = equal_of_buttons
    entry["equal_of_choice"] = equal_of_choice
    entry["in_form_true"] = in_form_true
    if isinstance(fields, Unknown):
        entry["fields"] = fields.reason.value
        fields_equal = (
            isinstance(their_fields, dict) and their_fields.get("unknown") == entry["fields"]
        )
    else:
        entry["fields"] = len(fields)
        fields_equal = their_fields == len(fields)
    entry["fields_equal"] = fields_equal
    differing: list[list] = []
    if not isinstance(fields, Unknown):
        by_ref = {
            (one.ref.object_number, one.ref.generation): one
            for one in fields
            if one.ref.object_number is not None
        }
        for page in pages:
            for widget in page:
                if isinstance(widget.field, Unknown):
                    continue
                number, generation = widget.field.ref.object_number, widget.field.ref.generation
                if by_ref.get((number, generation)) != widget.field:
                    differing.append([number, generation])
    entry["field_differences"] = differing
    return entry


def exit_status(receipt: dict) -> int:
    """Decision 4's 0 or 1."""
    if receipt["errors"]:
        return 1
    for entry in receipt["files"].values():
        if (
            entry["differences"]
            or entry["field_differences"]
            or not entry["stable"]
            or not entry["fields_equal"]
        ):
            return 1
    return 0


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="The M2 gate: widgets and fields of the corpus.")
    parser.add_argument("--commit", required=True, type=commit_argument, metavar="SHA")
    parser.add_argument("--out", required=True, type=Path, help="the receipt to write")
    parser.add_argument("--only", nargs="+", default=[], metavar="ID", help="manifest ids")
    parser.add_argument("--corpus", type=Path, default=ROOT / "bench" / "corpus" / "files")
    arguments = parser.parse_args(argv)

    with (ROOT / "bench" / "corpus" / "manifest.csv").open(encoding="utf-8", newline="") as handle:
        rows = list(csv.DictReader(handle))
    if arguments.only:
        known = {row["id"] for row in rows}
        unknown = [given for given in dict.fromkeys(arguments.only) if given not in known]
        if unknown:
            print(", ".join(unknown) + ": not in the manifest", file=sys.stderr)
            return 2
        wanted = set(arguments.only)
        rows = [row for row in rows if row["id"] in wanted]

    checked: dict[str, bytes] = {}
    for row in rows:
        path = arguments.corpus / row["file"]
        try:
            data = path.read_bytes()
        except OSError as error:
            print(f"{row['file']}: cannot be read: {error}", file=sys.stderr)
            return 2
        if hashlib.sha256(data).hexdigest() != row["sha256"]:
            print(f"{row['file']}: the SHA-256 is not {row['sha256']}", file=sys.stderr)
            return 2
        checked[row["file"]] = data

    runtime.start()
    files: dict[str, dict] = {}
    rich: dict[str, dict] = {}
    errors: dict[str, str] = {}
    started = time.perf_counter()
    for row in rows:
        name = row["file"]
        try:
            entry = check_file(checked[name])
        except Exception as error:
            print(f"error {name} {type(error).__name__}")
            print(f"{name}: {error}", file=sys.stderr)
            errors[name] = type(error).__name__
        else:
            rich[name] = entry
            files[name] = {key: entry[key] for key in RECEIPT_FILE_KEYS}
            print(f"ok {name} {entry['widgets']} widgets {len(entry['differences'])} differences")
    seconds = round(time.perf_counter() - started, 1)

    receipt = {
        "environment": {
            "commit": arguments.commit,
            "date": datetime.now(UTC).isoformat(timespec="seconds"),
            "engine_info": engine_info().as_dict(),
            "machine": platform.machine(),
            "platform": platform.platform(),
            "python": platform.python_version(),
        },
        "errors": errors,
        "files": files,
        "partial": bool(arguments.only),
        "seconds": seconds,
    }
    arguments.out.parent.mkdir(parents=True, exist_ok=True)
    arguments.out.write_text(json.dumps(receipt, indent=1, sort_keys=True) + "\n", encoding="utf-8")

    _print_summary(len(rows), rich, errors)
    print(f"receipt {arguments.out}")
    status = exit_status(receipt)
    return status


def _print_summary(count: int, rich: dict[str, dict], errors: dict[str, str]) -> None:
    """Decision 5's lines, from what the run collected."""
    facts = m2_referee.FACTS
    widgets = sum(entry["widgets"] for entry in rich.values())
    differences_count = sum(len(entry["differences"]) for entry in rich.values())
    print(f"{count} files, {widgets} widgets compared, {differences_count} differences")
    equal = {fact: sum(entry["equal"][fact] for entry in rich.values()) for fact in facts}
    known = {fact: sum(entry["known"][fact] for entry in rich.values()) for fact in facts}
    print("equal per fact: " + ", ".join(f"{fact} {equal[fact]}" for fact in facts))
    in_form_true = sum(entry["in_form_true"] for entry in rich.values())
    print(
        "known "
        + ", ".join(f"{fact} {known[fact]}" for fact in facts)
        + f"; in_form true {in_form_true}"
    )
    types: dict[str, int] = {}
    for entry in rich.values():
        for kind, number in entry["types"].items():
            types[kind] = types.get(kind, 0) + number
    buttons = {"states": 0, "state": 0, "export_value": 0}
    choice = {"options": 0}
    for entry in rich.values():
        for fact, number in entry["equal_of_buttons"].items():
            buttons[fact] += number
        for fact, number in entry["equal_of_choice"].items():
            choice[fact] += number
    print(
        f"buttons: checkbox {types.get('checkbox', 0)}, radio {types.get('radio', 0)}; "
        f"states equal {buttons['states']}, state equal {buttons['state']}, "
        f"export_value equal {buttons['export_value']}"
    )
    print(
        f"choice fields: {types.get('combo-box', 0) + types.get('list-box', 0)}; "
        f"options equal {choice['options']}"
    )
    unknown: dict[str, int] = {}
    unknown_types: dict[str, dict[str, int]] = {}
    for entry in rich.values():
        for key, number in entry["unknown"].items():
            unknown[key] = unknown.get(key, 0) + number
        for key, by_type in entry["unknown_types"].items():
            merged = unknown_types.setdefault(key, {})
            for kind, number in by_type.items():
                merged[kind] = merged.get(kind, 0) + number
    parts = []
    for key in sorted(unknown):
        part = f"{key} {unknown[key]}"
        by_type = unknown_types.get(key)
        if by_type:
            part += " (" + ", ".join(f"{kind} {by_type[kind]}" for kind in sorted(by_type)) + ")"
        parts.append(part)
    print("unknown: " + (", ".join(parts) if parts else "none"))
    stable = sum(1 for entry in rich.values() if entry["stable"])
    print(f"stable: {stable} of {count} files")
    fields_files = sum(1 for entry in rich.values() if entry["fields"])
    fields_total = sum(
        entry["fields"] for entry in rich.values() if isinstance(entry["fields"], int)
    )
    fields_equal = sum(1 for entry in rich.values() if entry["fields_equal"])
    field_differences = sum(len(entry["field_differences"]) for entry in rich.values())
    print(
        f"fields: {fields_total} in {fields_files} files, equal to the referee's count in "
        f"{fields_equal} of {count} files, {field_differences} differing from a widget's field"
    )


if __name__ == "__main__":
    raise SystemExit(main())

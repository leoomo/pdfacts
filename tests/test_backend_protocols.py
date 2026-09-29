"""The plain data and the failure type that both backends share."""

from __future__ import annotations

import ast
import dataclasses
import pickle
from pathlib import Path

import pytest

import pdfacts.backends.protocols as protocols
from pdfacts.backends.protocols import (
    EngineFailure,
    FailureKind,
    PageObject,
    RawBitmap,
    RecoveredBytes,
)


def test_the_protocols_import_no_engine_and_nothing_from_the_core():
    tree = ast.parse(Path(protocols.__file__).read_text())
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.add(node.module or "")
    assert imported, "no imports found; the scan is broken"
    assert not {
        name
        for name in imported
        if name.split(".")[0] in {"pypdfium2", "pypdfium2_raw", "pikepdf", "pdfacts"}
    }


def test_a_failure_carries_its_kind_and_detail():
    error = EngineFailure(FailureKind.FORMAT, "no header")
    assert (error.kind, error.detail) == (FailureKind.FORMAT, "no header")
    assert str(error) == "format: no header"


def test_a_failure_takes_the_name_of_a_kind():
    assert EngineFailure("password", "wrong").kind is FailureKind.PASSWORD
    with pytest.raises(ValueError):
        EngineFailure("unknown kind", "detail")


def test_a_failure_survives_pickling():
    error = pickle.loads(pickle.dumps(EngineFailure(FailureKind.OTHER, "closed")))
    assert (type(error), error.kind, error.detail) == (EngineFailure, FailureKind.OTHER, "closed")


def test_the_kinds():
    assert [kind.value for kind in FailureKind] == ["format", "password", "other"]


def test_the_page_object_has_the_fields_of_issue_10_and_is_frozen():
    names = [field.name for field in dataclasses.fields(PageObject)]
    assert names == [
        "object_number",
        "generation",
        "media_box",
        "crop_box",
        "rotate",
        "user_unit",
        "user_unit_invalid",
    ]
    page = PageObject(4, 0, (0.0, 0.0, 400.0, 300.0), None, None, None, False)
    with pytest.raises(dataclasses.FrozenInstanceError):
        page.rotate = 90  # type: ignore[misc]


def test_the_raw_bitmap_has_the_fields_of_issue_12_and_is_frozen():
    names = [field.name for field in dataclasses.fields(RawBitmap)]
    assert names == ["width", "height", "row_length", "channels", "data"]
    bitmap = RawBitmap(1, 1, 1, 1, b"\x00")
    with pytest.raises(dataclasses.FrozenInstanceError):
        bitmap.width = 2  # type: ignore[misc]


def test_the_recovered_bytes_have_the_fields_of_issue_13_and_are_frozen():
    names = [field.name for field in dataclasses.fields(RecoveredBytes)]
    assert names == ["data", "page_count", "warnings"]
    recovered = RecoveredBytes(b"%PDF-1.7", 1, ())
    with pytest.raises(dataclasses.FrozenInstanceError):
        recovered.page_count = 2  # type: ignore[misc]
    assert "PDF" not in repr(recovered)

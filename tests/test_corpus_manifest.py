"""The corpus manifest is consistent, and the fetch script treats every source state correctly.

No test here uses the network or a corpus file. Downloads are replaced by
functions that return bytes built in the test.
"""

from __future__ import annotations

import csv
import hashlib
import re
import sys
import urllib.error
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bench" / "corpus"))

import fetch  # noqa: E402

PDF = b"%PDF-1.7\nbuilt in a test\n"
OTHER_PDF = b"%PDF-1.7\nother bytes\n"


def manifest() -> list[dict[str, str]]:
    with (ROOT / "bench" / "corpus" / "manifest.csv").open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def row(data: bytes = PDF, *, url: str = "https://forms.example/a.pdf", state: str = "verified"):
    return {
        "id": "01",
        "file": "a.pdf",
        "bytes": str(len(data)),
        "sha256": hashlib.sha256(data).hexdigest(),
        "source_url": url,
        "source_state": state,
    }


class FakeTime:
    def __init__(self) -> None:
        self.now = 1000.0
        self.slept: list[float] = []

    def clock(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += seconds


def must_not_be_called(url: str) -> bytes:
    raise AssertionError(f"a download was attempted: {url}")


def test_manifest_has_the_expected_shape():
    rows = manifest()
    assert len(rows) == 104
    assert list(rows[0]) == [
        "id", "category", "title", "file", "bytes", "sha256",
        "source_url", "source_state", "source_checked",
    ]  # fmt: skip


def test_manifest_entries_are_unique_and_well_formed():
    rows = manifest()
    assert len({r["id"] for r in rows}) == len(rows)
    assert len({r["file"] for r in rows}) == len(rows)
    assert len({r["sha256"] for r in rows}) == len(rows)
    for r in rows:
        assert re.fullmatch(r"[0-9a-f]{64}", r["sha256"]), r["id"]
        assert int(r["bytes"]) > 0, r["id"]
        assert r["file"].endswith(".pdf") and "/" not in r["file"], r["id"]
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", r["source_checked"]), r["id"]


def test_source_state_and_address_fit_together():
    for r in manifest():
        assert r["source_state"] in fetch.SOURCE_STATES, r["id"]
        if r["source_state"] == "local-only":
            assert r["source_url"] == "", r["id"]
        else:
            assert r["source_url"].startswith("https://"), r["id"]


def test_only_a_verified_source_is_used():
    assert fetch.verified_source(row(state="verified")) == "https://forms.example/a.pdf"
    assert fetch.verified_source(row(state="changed")) == ""
    assert fetch.verified_source(row(state="local-only", url="")) == ""


def test_a_manifest_without_the_state_column_still_works():
    old = row()
    del old["source_state"]
    assert fetch.verified_source(old) == "https://forms.example/a.pdf"


def test_download_writes_the_file_when_the_hash_matches(tmp_path):
    state, _ = fetch.process(
        row(), files=tmp_path, local=None, network=True, pacer=fetch.Pacer(), request=lambda u: PDF
    )
    assert state == "ok"
    assert (tmp_path / "a.pdf").read_bytes() == PDF


def test_other_bytes_are_never_accepted_in_place_of_the_listed_file(tmp_path):
    state, _ = fetch.process(
        row(),
        files=tmp_path,
        local=None,
        network=True,
        pacer=fetch.Pacer(),
        request=lambda u: OTHER_PDF,
    )
    assert state == "changed"
    assert not (tmp_path / "a.pdf").exists()
    assert (tmp_path / "a.pdf.changed").read_bytes() == OTHER_PDF


@pytest.mark.parametrize("state", ["changed", "local-only"])
def test_an_entry_without_verified_source_is_not_downloaded(tmp_path, state):
    url = "" if state == "local-only" else "https://forms.example/a.pdf"
    result, note = fetch.process(
        row(state=state, url=url),
        files=tmp_path,
        local=None,
        network=True,
        pacer=fetch.Pacer(),
        request=must_not_be_called,
    )
    assert result == "no-source"
    assert state in note


def test_an_entry_without_verified_source_can_be_supplied_from_a_folder(tmp_path):
    local = tmp_path / "local"
    files = tmp_path / "files"
    local.mkdir()
    files.mkdir()
    (local / "a.pdf").write_bytes(PDF)
    result, _ = fetch.process(
        row(state="changed"),
        files=files,
        local=local,
        network=True,
        pacer=fetch.Pacer(),
        request=must_not_be_called,
    )
    assert result == "ok"
    assert (files / "a.pdf").read_bytes() == PDF


def test_a_local_file_with_other_bytes_is_not_copied(tmp_path):
    local = tmp_path / "local"
    files = tmp_path / "files"
    local.mkdir()
    files.mkdir()
    (local / "a.pdf").write_bytes(OTHER_PDF)
    result, _ = fetch.process(
        row(state="local-only", url=""),
        files=files,
        local=local,
        network=False,
        pacer=fetch.Pacer(),
        request=must_not_be_called,
    )
    assert result == "no-source"
    assert not (files / "a.pdf").exists()


def test_a_response_that_is_not_a_pdf_is_a_failure(tmp_path):
    state, note = fetch.process(
        row(),
        files=tmp_path,
        local=None,
        network=True,
        pacer=fetch.Pacer(),
        request=lambda u: b"<html>landing page</html>",
    )
    assert state == "failed"
    assert "not a PDF" in note
    assert list(tmp_path.iterdir()) == []


def test_checking_a_source_writes_nothing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    assert fetch.check_source(row(), pacer=fetch.Pacer(), request=lambda u: PDF) == ("ok", "")
    state, note = fetch.check_source(row(), pacer=fetch.Pacer(), request=lambda u: OTHER_PDF)
    assert state == "changed"
    assert str(len(OTHER_PDF)) in note
    assert fetch.check_source(
        row(state="changed"), pacer=fetch.Pacer(), request=must_not_be_called
    ) == ("skipped", "no verified source")
    assert list(tmp_path.iterdir()) == []


def test_requests_to_one_host_are_spaced():
    fake = FakeTime()
    pacer = fetch.Pacer(clock=fake.clock, sleep=fake.sleep)
    pacer.wait("https://forms.example/a.pdf")
    pacer.wait("https://forms.example/b.pdf")
    pacer.wait("https://other.example/c.pdf")
    assert fake.slept == [fetch.HOST_PAUSE_SECONDS]


def test_no_pause_is_added_when_enough_time_has_passed():
    fake = FakeTime()
    pacer = fetch.Pacer(clock=fake.clock, sleep=fake.sleep)
    pacer.wait("https://forms.example/a.pdf")
    fake.now += fetch.HOST_PAUSE_SECONDS + 1
    pacer.wait("https://forms.example/b.pdf")
    assert fake.slept == []


def test_a_rate_limit_slows_the_host_down_and_is_retried_once():
    fake = FakeTime()
    pacer = fetch.Pacer(clock=fake.clock, sleep=fake.sleep)
    calls: list[str] = []

    def limited_once(url: str) -> bytes:
        calls.append(url)
        if len(calls) == 1:
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", None, None)
        return PDF

    assert fetch.download("https://forms.example/a.pdf", pacer, limited_once) == PDF
    assert len(calls) == 2
    assert fake.slept == [fetch.HOST_PAUSE_AFTER_LIMIT_SECONDS]


def test_a_second_rate_limit_is_reported():
    fake = FakeTime()
    pacer = fetch.Pacer(clock=fake.clock, sleep=fake.sleep)

    def always_limited(url: str) -> bytes:
        raise urllib.error.HTTPError(url, 429, "Too Many Requests", None, None)

    state, note = fetch.check_source(row(), pacer=pacer, request=always_limited)
    assert state == "failed"
    assert "429" in note

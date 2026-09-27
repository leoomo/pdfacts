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
from email.message import Message
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


def limited(times: int, calls: list[str], retry_after: str | None = None):
    """A host that answers with status 429 for the first `times` requests."""

    def request(url: str) -> bytes:
        calls.append(url)
        if len(calls) <= times:
            headers = Message()
            if retry_after is not None:
                headers["Retry-After"] = retry_after
            raise urllib.error.HTTPError(url, 429, "Too Many Requests", headers, None)
        return PDF

    return request


def test_a_rate_limit_is_answered_by_waiting_and_trying_again():
    fake = FakeTime()
    pacer = fetch.Pacer(clock=fake.clock, sleep=fake.sleep)
    calls: list[str] = []
    assert fetch.download("https://forms.example/a.pdf", pacer, limited(1, calls)) == PDF
    assert len(calls) == 2
    assert fake.slept == [fetch.RETRY_PAUSES_SECONDS[0]]


def test_a_second_rate_limit_is_answered_by_a_longer_wait():
    fake = FakeTime()
    pacer = fetch.Pacer(clock=fake.clock, sleep=fake.sleep)
    calls: list[str] = []
    assert fetch.download("https://forms.example/a.pdf", pacer, limited(2, calls)) == PDF
    assert len(calls) == 3
    assert fake.slept == list(fetch.RETRY_PAUSES_SECONDS)


def test_a_host_that_keeps_limiting_is_reported_after_two_retries():
    fake = FakeTime()
    pacer = fetch.Pacer(clock=fake.clock, sleep=fake.sleep)
    calls: list[str] = []
    state, note = fetch.check_source(row(), pacer=pacer, request=limited(99, calls))
    assert state == "failed"
    assert "429" in note
    assert len(calls) == 1 + len(fetch.RETRY_PAUSES_SECONDS)


def test_the_slower_pace_is_kept_for_the_next_file_of_the_host():
    fake = FakeTime()
    pacer = fetch.Pacer(clock=fake.clock, sleep=fake.sleep)
    calls: list[str] = []
    request = limited(1, calls)
    fetch.download("https://forms.example/a.pdf", pacer, request)
    fetch.download("https://forms.example/b.pdf", pacer, request)
    assert fake.slept == [fetch.RETRY_PAUSES_SECONDS[0]] * 2


def test_the_wait_the_host_asks_for_is_kept_when_it_is_longer():
    fake = FakeTime()
    pacer = fetch.Pacer(clock=fake.clock, sleep=fake.sleep)
    calls: list[str] = []
    fetch.download("https://forms.example/a.pdf", pacer, limited(1, calls, retry_after="45"))
    assert fake.slept == [45.0]


def test_the_wait_the_host_asks_for_has_an_upper_bound():
    fake = FakeTime()
    pacer = fetch.Pacer(clock=fake.clock, sleep=fake.sleep)
    calls: list[str] = []
    fetch.download("https://forms.example/a.pdf", pacer, limited(1, calls, retry_after="86400"))
    assert fake.slept == [fetch.MAX_RETRY_AFTER_SECONDS]


def test_other_http_errors_are_not_retried():
    fake = FakeTime()
    pacer = fetch.Pacer(clock=fake.clock, sleep=fake.sleep)
    calls: list[str] = []

    def gone(url: str) -> bytes:
        calls.append(url)
        raise urllib.error.HTTPError(url, 404, "Not Found", Message(), None)

    state, note = fetch.check_source(row(), pacer=pacer, request=gone)
    assert (state, len(calls)) == ("failed", 1)
    assert "404" in note


def host(robots: bytes | int, calls: list[str]):
    """A host whose robots.txt has the given content, or answers with the given status."""

    def request(url: str) -> bytes:
        calls.append(url)
        if url.endswith("/robots.txt"):
            if isinstance(robots, int):
                raise urllib.error.HTTPError(url, robots, "status", Message(), None)
            return robots
        return PDF

    return request


def checked(robots: bytes | int, calls: list[str], fake: FakeTime | None = None):
    fake = fake or FakeTime()
    pacer = fetch.Pacer(clock=fake.clock, sleep=fake.sleep)
    request = host(robots, calls)
    rules = fetch.Robots(pacer, request)
    return fetch.check_source(row(), pacer=pacer, request=request, robots=rules)


def test_an_address_the_host_disallows_is_not_fetched():
    calls: list[str] = []
    state, note = checked(b"User-agent: *\nDisallow: /\n", calls)
    assert state == "failed"
    assert "robots.txt" in note
    assert calls == ["https://forms.example/robots.txt"]


def test_a_rule_for_this_client_by_name_is_kept():
    calls: list[str] = []
    rules = b"User-agent: pdfacts-corpus-fetch\nDisallow: /a.pdf\n\nUser-agent: *\nDisallow:\n"
    assert checked(rules, calls)[0] == "failed"
    assert calls == ["https://forms.example/robots.txt"]


def test_an_address_the_host_allows_is_fetched():
    calls: list[str] = []
    assert checked(b"User-agent: *\nDisallow: /private/\n", calls) == ("ok", "")
    assert calls == ["https://forms.example/robots.txt", "https://forms.example/a.pdf"]


def test_a_host_without_robots_file_has_no_rules():
    calls: list[str] = []
    assert checked(404, calls) == ("ok", "")


@pytest.mark.parametrize("status", [429, 500, 503])
def test_nothing_is_fetched_when_the_rules_cannot_be_read(status):
    calls: list[str] = []
    state, note = checked(status, calls)
    assert state == "failed"
    assert "robots.txt" in note
    assert calls == ["https://forms.example/robots.txt"]


def test_the_rules_of_a_host_are_read_once():
    fake = FakeTime()
    pacer = fetch.Pacer(clock=fake.clock, sleep=fake.sleep)
    calls: list[str] = []
    request = host(b"User-agent: *\nDisallow:\n", calls)
    rules = fetch.Robots(pacer, request)
    for name in ("a.pdf", "b.pdf", "c.pdf"):
        fetch.download(f"https://forms.example/{name}", pacer, request, rules)
    assert calls.count("https://forms.example/robots.txt") == 1
    assert len(calls) == 4


def test_a_crawl_delay_stated_by_the_host_is_kept():
    fake = FakeTime()
    pacer = fetch.Pacer(clock=fake.clock, sleep=fake.sleep)
    calls: list[str] = []
    request = host(b"User-agent: *\nCrawl-delay: 10\nDisallow:\n", calls)
    rules = fetch.Robots(pacer, request)
    fetch.download("https://forms.example/a.pdf", pacer, request, rules)
    fetch.download("https://forms.example/b.pdf", pacer, request, rules)
    # robots.txt, then the first file, then the second: two intervals of ten seconds
    assert fake.slept == [10.0, 10.0]


def test_a_shorter_crawl_delay_does_not_shorten_the_pause():
    fake = FakeTime()
    pacer = fetch.Pacer(clock=fake.clock, sleep=fake.sleep)
    calls: list[str] = []
    request = host(b"User-agent: *\nCrawl-delay: 1\nDisallow:\n", calls)
    rules = fetch.Robots(pacer, request)
    fetch.download("https://forms.example/a.pdf", pacer, request, rules)
    assert fake.slept == [fetch.HOST_PAUSE_SECONDS]

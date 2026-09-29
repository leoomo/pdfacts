"""The decoders of text strings and names, written from ISO 32000-2.

Every expected value is written out from the rules of the issue, not produced
by the code under test (AGENTS.md, rule 2).
"""

from __future__ import annotations

import dataclasses

import pytest

from pdfacts.core.strings import (  # noqa: E402
    Name,
    NameEncoding,
    decode_name,
    decode_text_string,
)


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (b"Label", "Label"),
        (bytes(range(0x18)), "".join(map(chr, range(0x18)))),
        (bytes(range(0x18, 0x20)), "˘ˇˆ˙˝˛˚˜"),
        (bytes([0x80, 0x84, 0x8D, 0x92, 0x93, 0x9E, 0xA0]), "•—“™ﬁž€"),
        (bytes(range(0xA1, 0xAD)) + bytes(range(0xAE, 0x100)), None),
        (b"a\x7fb", "a\ufffdb"),
        (b"a\x9fb", "a\ufffdb"),
        (b"a\xadb", "a\ufffdb"),
        (b"\xfe\xff\x00L\x00\xe4", "Lä"),
        (b"\xfe\xff\xd8\x3d\xde\x00", "\U0001f600"),
        (b"\xfe\xff\x00L\x00", "L\ufffd"),
        (b"\xfe\xff\xd8\x00\x00L", "\ufffdL"),
        (b"\xfe\xff\xdc\x00\x00L", "\ufffdL"),
        (b"\xff\xfeL\x00\xe4\x00", "Lä"),
        (b"\xef\xbb\xbfL\xc3\xa4", "Lä"),
        (b"\xef\xbb\xbfL\xc3(", "L\ufffd("),
        (b"\xfe\xff\x00\x1b\x00d\x00e\x00\x1b\x00L\x00\xe4", "Lä"),
        (b"\xfe\xff\x00\x1b\x00d\x00e\x00D\x00E\x00\x1b\x00L", "L"),
        (b"\xef\xbb\xbf\x1bde\x1bL\xc3\xa4", "Lä"),
        (b"\xff\xfe\x1b\x00d\x00e\x00\x1b\x00L\x00", "L"),
        (b"\xfe\xff\x00L\x00\x1b\x00d\x00e", "L"),
        (b"a\x1bb", "a˙b"),
        (b"", ""),
        (b"\xfe\xff", ""),
        (b"\xef\xbb\xbf", ""),
    ],
)
def test_decode_text_string(data, expected):
    if expected is None:
        expected = data.decode("latin-1")
    assert decode_text_string(data) == expected


@pytest.mark.parametrize("data", ["Label", bytearray(b"Label"), None])
def test_decode_text_string_takes_bytes_only(data):
    with pytest.raises(TypeError):
        decode_text_string(data)


def test_ascii_name_is_utf8():
    assert decode_name(b"Ja") == Name(b"Ja", "Ja", NameEncoding.UTF8)


def test_utf8_name():
    name = decode_name(b"M\xc3\xb6nnlich")
    assert name.text == "Mönnlich"
    assert name.encoding is NameEncoding.UTF8


def test_latin1_name():
    name = decode_name(b"M\xf6nnlich")
    assert name.text == "Mönnlich"
    assert name.encoding is NameEncoding.LATIN1


def test_empty_name():
    assert decode_name(b"") == Name(b"", "", NameEncoding.UTF8)


def test_decode_name_takes_bytes_only():
    with pytest.raises(TypeError):
        decode_name("Ja")


def test_the_encoding_by_value():
    assert Name(b"Ja", "Ja", "utf-8").encoding is NameEncoding.UTF8


@pytest.mark.parametrize(
    ("raw", "text", "encoding"),
    [
        (b"Ja", "Ja", NameEncoding.LATIN1),
        (b"M\xf6", "Mö", NameEncoding.UTF8),
        (b"Ja", "Nein", NameEncoding.UTF8),
        (b"Ja", "Ja", "utf-16"),
    ],
)
def test_a_name_whose_text_is_not_its_decoding(raw, text, encoding):
    with pytest.raises(ValueError):
        Name(raw, text, encoding)


@pytest.mark.parametrize(
    "build", [lambda: Name("Ja", "Ja", "utf-8"), lambda: Name(b"Ja", b"Ja", "utf-8")]
)
def test_a_name_of_wrong_types(build):
    with pytest.raises(TypeError):
        build()


def test_a_name_is_frozen():
    name = decode_name(b"Ja")
    with pytest.raises(dataclasses.FrozenInstanceError):
        name.text = "Nein"

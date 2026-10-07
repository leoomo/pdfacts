"""Write src/pdfacts/backends/adobe_fonts.py from Adobe's files.

The sources, which are not kept in this repository:

- the Core 14 AFM files, Core14_AFMs.zip, from
  https://download.macromedia.com/pub/developer/opentype/tech-notes/Core14_AFMs.zip
  (SHA-256 below), with their ReadMe, MustRead.html;
- the Adobe Glyph List, glyphlist.txt and zapfdingbats.txt of
  https://github.com/adobe-type-tools/agl-aglfn (BSD-3-Clause; SHA-256 below).

Usage: python tools/adobe_fonts.py <directory of the AFM files> <directory of agl-aglfn>

It checks the files against their SHA-256 first, and writes the module only
from files that match.
"""

from __future__ import annotations

import hashlib
import html
import pprint
import re
import sys
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "src" / "pdfacts" / "backends" / "adobe_fonts.py"

AFM_FILES = {
    "Courier.afm": "521e0d7c7521efd4be78a5a9c5398e4c67d0771e396115b0346bc4ef74ada53d",
    "Courier-Bold.afm": "ad0150d4bedcc8877742bf94251fcec13e348dd599d4603f679d92027d1e6e99",
    "Courier-BoldOblique.afm": "cb82e69ef5f6d421e8f404fe00bb0d993425aae79725331d0ebf847e94e97e92",
    "Courier-Oblique.afm": "b27103b2a2ef6030c110626597e2ab47bb8279075a039ab9170facb0aa1f70e1",
    "Helvetica.afm": "da33f1870474c8e68bfe3e2353ff107ab6c6eea1f9836ce2aaf1e1a07b17982f",
    "Helvetica-Bold.afm": "b880d96baf56d0cc059f258f60b4d764ef49b555ab9db294b959c0016dee41f2",
    "Helvetica-BoldOblique.afm": "69984a35ca26973a39f261cf83e0d367ea2e6517c590b0b030d4d4a219d9c269",
    "Helvetica-Oblique.afm": "b4609b71b660a392ac09df35060271a876c2ce66617dd83bf826f742bb9d9721",
    "Symbol.afm": "3d2128a820375a10de9bc8bf6cfb15ded482c01ca0f95cc0b3277f37ec8bde66",
    "Times-Bold.afm": "b4a000ed85cb22c6cdd985aa0fd3f6f78ed5079b7c6860dc4f0234e0d0e3c522",
    "Times-BoldItalic.afm": "93c4744ba955215de02c4aae0b777133442ada2f7ab5a2af30a040b792b3c55d",
    "Times-Italic.afm": "ed37fa2e6a67b5b17dfd47f36fc7e90df32891a4408860dbc8d4cbbe9959e242",
    "Times-Roman.afm": "768e1cabea085d489a63da3e80b96bc5abf0ec98d3073c9b4d6ba76e7bccba64",
    "ZapfDingbats.afm": "a32565c90afd1b57a7008fc567b78d95cf1c22adff5e086094d666d88b039859",
    "MustRead.html": "be71e055bb2551a6fc8b60dcf094bf40357858980abde86f76cc9a3c144d5403",
}
AGL_FILES = {
    "glyphlist.txt": "a3b2f61ced9f3644cc0d4ecde5c59df34ca286c689d9484a43a710a81c466789",
    "zapfdingbats.txt": "f6394e3cb8a447e84a1dad75d4baaf2aa7f45dc104faf369f4720e1a774ef2dc",
    "LICENSE.md": "58147d341e7a34aa2196862395a34d2fd95716c41d5ed26efb59ab0e12f92089",
}

# ISO 32000-1, Annex D, notes to the table of the Latin character set: in
# WinAnsiEncoding, space is also at 0240 and hyphen at 0255, and every unused
# code above 040 is bullet; in MacRomanEncoding, space is also at 0312 and
# currency is at 0333.
WIN_ANSI_ALSO = {
    0xA0: "space",
    0xAD: "hyphen",
    0x7F: "bullet",
    0x81: "bullet",
    0x8D: "bullet",
    0x8F: "bullet",
    0x90: "bullet",
    0x9D: "bullet",
}
MAC_ROMAN_ALSO = {0xCA: "space", 0xDB: "currency"}


def checked(directory: Path, files: dict[str, str]) -> dict[str, bytes]:
    out = {}
    for name, digest in files.items():
        data = (directory / name).read_bytes()
        found = hashlib.sha256(data).hexdigest()
        if digest and found != digest:
            raise SystemExit(f"{name}: SHA-256 {found}, not {digest}")
        out[name] = data
    return out


def glyph_list(text: str) -> dict[str, str]:
    """name;XXXX[ XXXX...] lines: a name and the text it stands for."""
    out: dict[str, str] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        name, codes = line.split(";")
        out[name] = "".join(chr(int(code, 16)) for code in codes.split())
    return out


def afm(text: str) -> dict:
    """What the module keeps of one AFM file."""
    font = {"copyright": [], "codes": {}, "widths": {}}
    for line in text.splitlines():
        key, _, rest = line.partition(" ")
        if key == "Comment" and rest.startswith("Copyright"):
            font["copyright"].append(rest)
        elif key == "FontName":
            font["name"] = rest.strip()
        elif key in ("Ascender", "Descender"):
            font[key] = int(rest)
        elif key == "FontBBox":
            font["bbox"] = [int(v) for v in rest.split()]
        elif key == "C":
            fields = dict(part.strip().split(" ", 1) for part in line.split(";") if part.strip())
            code, name, width = int(fields["C"]), fields["N"], int(float(fields["WX"]))
            font["widths"][name] = width
            if code >= 0:
                font["codes"][code] = name
    return font


def encoding_from_codec(codec: str, names: set[str], glyphs: dict[str, str], also: dict[int, str]):
    """For each code, the glyph name of the Latin font whose text the codec
    gives for it; `also` where Annex D says otherwise; None for none."""
    by_text: dict[str, list[str]] = {}
    for name, text in glyphs.items():
        if name in names:
            by_text.setdefault(text, []).append(name)
    table: list[str | None] = []
    for code in range(256):
        if code in also:
            table.append(also[code])
            continue
        if code < 0x20:
            table.append(None)
            continue
        try:
            text = bytes([code]).decode(codec)
        except UnicodeDecodeError:
            table.append(None)
            continue
        found = by_text.get(text, [])
        table.append(found[0] if len(found) == 1 else None)
    return table


def literal(name: str, value) -> list[str]:
    """`name = value`, wrapped to lines of at most 100 characters."""
    text = pprint.pformat(value, width=96 - len(name) - 3, compact=True, sort_dicts=False)
    lines = f"{name} = {text}".splitlines()
    assert all(len(line) <= 100 for line in lines), name
    return lines


def main() -> None:
    afm_dir, agl_dir = Path(sys.argv[1]), Path(sys.argv[2])
    afm_files = checked(afm_dir, AFM_FILES)
    agl_files = checked(agl_dir, AGL_FILES)
    glyphs = glyph_list(agl_files["glyphlist.txt"].decode("ascii"))
    dingbats = glyph_list(agl_files["zapfdingbats.txt"].decode("ascii"))
    fonts = {}
    for name in AFM_FILES:
        if name.endswith(".afm"):
            font = afm(afm_files[name].decode("latin-1"))
            fonts[font["name"]] = font
    latin = set(fonts["Helvetica"]["widths"])
    standard = [fonts["Helvetica"]["codes"].get(code) for code in range(256)]
    win_ansi = encoding_from_codec("cp1252", latin, glyphs, WIN_ANSI_ALSO)
    mac_roman = encoding_from_codec("mac_roman", latin, glyphs, MAC_ROMAN_ALSO)
    readme = " ".join(
        html.unescape(re.sub(r"<[^>]*>", " ", afm_files["MustRead.html"].decode("latin-1"))).split()
    )
    start = readme.index("This file and the 14 PostScript")
    end = readme.index("support the use of the AFM files.") + len(
        "support the use of the AFM files."
    )
    notice = readme[start:end]
    license_text = agl_files["LICENSE.md"].decode("utf-8").strip()
    agl_copyright = next(
        line[2:].strip()
        for line in agl_files["glyphlist.txt"].decode("ascii").splitlines()
        if line.startswith("# Copyright")
    )
    copyrights = sorted({line for font in fonts.values() for line in font["copyright"]})

    lines = [
        '"""Metrics of the standard 14 fonts, their encodings, and the Adobe Glyph List.',
        "",
        "Generated by tools/adobe_fonts.py from Adobe's files; do not edit. What it",
        "holds is extracted, and so modified:",
        "",
        "- from the Core 14 AFM files: each font's name, Ascender, Descender and",
        "  FontBBox, and for each glyph its name, its width (WX) and its code;",
        "  everything else of the AFM files is left out;",
        "- from the Adobe Glyph List: glyphlist.txt and zapfdingbats.txt, each",
        "  name with the text it stands for.",
        "",
        "STANDARD_ENCODING is the codes of Helvetica.afm. WIN_ANSI_ENCODING and",
        "MAC_ROMAN_ENCODING are not Adobe's: they are the glyph names of",
        "Helvetica.afm whose text Python's cp1252 and mac_roman codecs give for each",
        "code, with what ISO 32000-1, Annex D, says otherwise.",
        "",
        "The Core 14 AFM files carry these notices:",
        "",
        *copyrights,
        "",
        "and their ReadMe, MustRead.html, says:",
        "",
        *[f"    {line}" for line in _wrapped(notice)],
        "",
        "The Adobe Glyph List carries this notice:",
        "",
        agl_copyright,
        "",
        *[f"    {line}" if line else "" for line in license_text.splitlines()],
        '"""',
        "",
        "# fmt: off",
        *literal("GLYPH_LIST", dict(sorted(glyphs.items()))),
        *literal("ZAPF_DINGBATS_GLYPH_LIST", dict(sorted(dingbats.items()))),
        *literal("STANDARD_ENCODING", tuple(standard)),
        *literal("WIN_ANSI_ENCODING", tuple(win_ansi)),
        *literal("MAC_ROMAN_ENCODING", tuple(mac_roman)),
        "# name: (Ascender or None, Descender or None, FontBBox, widths by glyph name,",
        "# the font's own encoding: glyph names by code)",
        *literal(
            "STANDARD_FONTS",
            {
                name: (
                    fonts[name].get("Ascender"),
                    fonts[name].get("Descender"),
                    tuple(fonts[name]["bbox"]),
                    dict(sorted(fonts[name]["widths"].items())),
                    tuple(fonts[name]["codes"].get(code) for code in range(256)),
                )
                for name in sorted(fonts)
            },
        ),
        "# fmt: on",
        "",
    ]
    OUT.write_text("\n".join(lines))
    print(f"wrote {OUT}: {len(glyphs)} glyph names, {len(dingbats)} dingbats names, {len(fonts)}")


def _wrapped(text: str, width: int = 72) -> list[str]:
    out, line = [], ""
    for word in text.split():
        if line and len(line) + 1 + len(word) > width:
            out.append(line)
            line = word
        else:
            line = f"{line} {word}" if line else word
    if line:
        out.append(line)
    return out


if __name__ == "__main__":
    main()

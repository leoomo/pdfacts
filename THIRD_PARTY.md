# Third-party components

pdfacts is a pure Python package. It bundles no third-party code and no
binaries; it bundles data extracted from two sets of Adobe's files, listed
under "Bundled data". The components below are installed as dependencies.
Anyone who distributes a product or image containing them has to carry their
notices.

License identifiers are taken from the metadata and license files of the
wheels named here (macOS arm64, read on 2026-09-27). Other platforms ship the
same projects but may vendor different system libraries; check the wheel you
actually deploy. This file is an inventory, not legal advice.

## Bundled data

`src/pdfacts/backends/adobe_fonts.py` holds data extracted from these files
by `tools/adobe_fonts.py`; the module carries their notices in full and
states what was extracted.

| Source | Version | License | What is extracted |
| --- | --- | --- | --- |
| Core 14 AFM files (`Core14_AFMs.zip`, Adobe) | as published, SHA-256 of each file in `tools/adobe_fonts.py` | Adobe's notice in their ReadMe: use, copy and distribute, with or without modification, keeping the copyright notices and this notice, modifications noted | each font's name, Ascender, Descender and FontBBox; each glyph's name, width and code |
| Adobe Glyph List (`glyphlist.txt`, `zapfdingbats.txt` of `adobe-type-tools/agl-aglfn`) | as published, SHA-256 in `tools/adobe_fonts.py` | BSD-3-Clause | each glyph name and the text it stands for |

## Runtime dependencies

| Component | Version | License | Role |
| --- | --- | --- | --- |
| pypdfium2 | 5.13.0 | Apache-2.0 or BSD-3-Clause | binding to PDFium |
| PDFium | 153.0.7999.0 (pdfium-binaries build, without V8 and XFA) | see the wheel's `pdfium.txt` | engine |
| pikepdf | 10.14.0 | MPL-2.0 | binding to QPDF |
| QPDF | 12.4.1 | Apache-2.0 | engine |
| Pillow | 12.3.0 | MIT-CMU | dependency of pikepdf |
| lxml | 6.1.3 | BSD-3-Clause | dependency of pikepdf |
| packaging | 26.3 | Apache-2.0 or BSD-2-Clause | dependency of pikepdf |

## Components inside the pypdfium2 wheel

The wheel ships one license file for each of these: abseil, agg23, fast_float,
FreeType, ICU, Little CMS, libjpeg-turbo, OpenJPEG, libpng, libtiff, llvm-libc,
simdutf, zlib, pdfium-binaries.

## Components inside the pikepdf wheel

According to the third-party README shipped in the wheel:

| Component | License | Where |
| --- | --- | --- |
| libjpeg-turbo | IJG, BSD-3-Clause and Zlib | all platforms |
| GCC runtime libraries | GPL-3.0-or-later with the GCC Runtime Library Exception 3.1 | Linux wheels |
| OpenSSL, zlib, Microsoft Visual C++ runtime | Apache-2.0, Zlib, Microsoft terms | Windows wheels only |
| sRGB ICC profile | Zlib | data file |
| veraPDF validation rules | CC-BY-4.0 | data file |
| pdfminer.six Latin encoding table | MIT | source file |

Linux wheels also carry a software bill of materials at
`pikepdf-<version>.dist-info/sboms/auditwheel.cdx.json`.

## What the licenses ask of this project

- MPL-2.0 (pikepdf): changes to pikepdf's own source files have to be published
  under the MPL. Using pikepdf unchanged as a dependency creates no such duty.
- The other licenses require that copyright and license notices are kept.

## Development tools

pytest and ruff are used for development only and are not part of any
distributed package. numpy 2.5.3 and scipy 1.18.1, in the dependency
group `dev`, serve the M4b gate's rendering measurement
(`bench/gates/m4b.py`) only; they are not part of any distributed
package.

## Libraries the gates compare with

The dependency group `bench` installs these for the gates in `bench/gates/`;
`bench/gates/run.py` installs the group. They are not dependencies of the
package and are not part of any distributed package. The library, its tests
and the tools never import them. Licenses are read from the wheels' metadata
and license files (macOS arm64, read on 2026-10-05).

| Component | Version | License | Role |
| --- | --- | --- | --- |
| pypdf | 6.19.0 | BSD-3-Clause | a third-party reader and writer in the M5a gate |
| cryptography | 50.0.2 | Apache-2.0 or BSD-3-Clause | AES for pypdf |
| cffi | 2.1.1 | MIT-0 | dependency of cryptography, not on PyPy |
| pycparser | 3.0 | BSD-3-Clause | dependency of cffi, not on PyPy |
| PyMuPDF | 1.28.2 (MuPDF 1.28.2) | AGPL-3.0 or Artifex's commercial license | a third-party reader and writer in the M5a gate |

The cryptography wheel carries software bills of materials in
`cryptography-<version>.dist-info/sboms/`: the OpenSSL built into it, 4.0.3 in
the wheel read, and the Rust crates it is built from. The PyMuPDF wheel ships
MuPDF in libraries of its own and lists nothing that is built into them.

## Not allowed as a dependency

MuPDF and its bindings (PyMuPDF, MuPDF.js), Ghostscript, Poppler, iText and
borb are licensed under the AGPL or GPL. They are not dependencies of the
library. PyMuPDF is installed for comparison under `bench/` only, in the group
`bench` above.

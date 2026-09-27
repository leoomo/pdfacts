# Third-party components

pdfacts is a pure Python package. It bundles no third-party code and no
binaries. The components below are installed as dependencies. Anyone who
distributes a product or image containing them has to carry their notices.

License identifiers are taken from the metadata and license files of the
wheels named here (macOS arm64, read on 2026-09-27). Other platforms ship the
same projects but may vendor different system libraries; check the wheel you
actually deploy. This file is an inventory, not legal advice.

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
distributed package.

## Not allowed as a dependency

MuPDF and its bindings (PyMuPDF, MuPDF.js), Ghostscript, Poppler, iText and
borb are licensed under the AGPL or GPL. They are not dependencies of the
library. PyMuPDF may be installed for comparison under `bench/` only.

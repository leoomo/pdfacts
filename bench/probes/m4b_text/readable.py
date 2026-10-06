"""Criterion 3 of the probe: every corpus file whose text pdfacts reads
through PDFium, read by the interpreter too. Prints, per file, pages that
PDFium reads and the interpreter does not, with the reason; and counts."""
import sys
from collections import Counter
from pathlib import Path

import pikepdf

import pdfacts
from interpreter import Unreadable, page_glyphs

files = sorted(Path(sys.argv[1]).glob("*.pdf"))
c = Counter()
for path in files:
    data = path.read_bytes()
    try:
        document = pdfacts.open(data)
    except pdfacts.PdfactsError as error:
        c["files pdfacts does not open"] += 1
        print(f"{path.name[:40]:40} pdfacts: {type(error).__name__}")
        continue
    pdf = pikepdf.open(path)
    with document:
        for page in document.pages:
            pdfium = pdfacts.glyphs(document, page.index)
            pdfium_reads = not isinstance(pdfium, pdfacts.Unknown)
            c["pages"] += 1
            c["pages PDFium reads"] += pdfium_reads
            try:
                found = page_glyphs(pdf, page.index)
                c["pages the interpreter reads"] += 1
                c["glyphs of the interpreter"] += len(found)
                c["glyphs without text"] += sum(g.text is None for g in found)
                c["glyphs without ascent or descent"] += sum(g.ascent is None or g.descent is None for g in found)
                if pdfium_reads:
                    c["glyphs of PDFium"] += len(pdfium)
            except Unreadable as error:
                c["pages the interpreter does not read"] += 1
                if pdfium_reads:
                    c["pages PDFium reads and the interpreter does not"] += 1
                    print(f"{path.name[:40]:40} page {page.index}: {error}")
            except Exception as error:  # a fault of the prototype, counted apart
                c["pages where the interpreter fails"] += 1
                print(f"{path.name[:40]:40} page {page.index}: FAULT {type(error).__name__}: {error}")
print(dict(c))

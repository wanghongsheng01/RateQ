"""Exit 0 if two matplotlib PDFs are byte-identical once the /CreationDate value is masked.

matplotlib stamps the wall-clock time into /CreationDate; everything else (drawing commands, fonts,
object layout) is deterministic for fixed library versions. Usage: python same_pdf.py a.pdf b.pdf
"""
import re
import sys

MASK = re.compile(rb"/CreationDate \(D:[^)]*\)")


def norm(p):
    with open(p, "rb") as f:
        return MASK.sub(b"/CreationDate (D:masked)", f.read())


sys.exit(0 if norm(sys.argv[1]) == norm(sys.argv[2]) else 1)

"""In-memory constancia builder for the §7a régime-determinacy cases.

The committed `constancia_situacion_fiscal.pdf` is the *valid* golden fixture. §7a also
requires the same document with a deliberately undetermined régime — no occurrence, no
code, a non-3-digit code, no description, a description that runs into the next CSF
field, or two occurrences — so those bytes are built here rather than committed as
blobs. Every other field is known-good, which means a test built from here can only
fail on the régime decision it is about.

The bytes go through the *real* `PdfCsfParser`, i.e. through pypdf's extracted text:
that is the layer §7a assigns the confidence rule to. reportlab stamps each file with a
creation instant, so a document's content is reproducible but its SHA-256 is computed
at run time and never committed.

Run:  python tests/fixtures/generate_csf_fixture.py   (the committed valid fixture)
"""

from __future__ import annotations

import io

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

#: Every field a constancia needs for a parse to reach the régime: RFC, name and
#: persona type. They are valid, so only the régime under test can fail the parse.
CONTEXT_LINES: tuple[str, ...] = (
    "CONSTANCIA DE SITUACIÓN FISCAL",
    "RFC: WATM640917J45",
    "Denominación o Razón Social: PRUEBA PERSONA FISICA",
    "Persona: Persona Física",
)

#: The field that follows the régime in a constancia — the one an unbounded régime
#: description used to swallow (§7a: "description spilling into the next CSF field").
NEXT_FIELD_LINE = "Situación Fiscal: Activo"


def build_constancia_bytes(*regimen_lines: str) -> bytes:
    """Build a one-page constancia whose régime field(s) are exactly `regimen_lines`.

    No line at all builds a constancia with no `Régimen Fiscal` field. One line builds
    the determinate/undetermined single-occurrence shape; two lines build the ambiguous
    double-occurrence shape. Lines are plain literal text (no reportlab markup).
    """
    styles = getSampleStyleSheet()
    buffer = io.BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=letter)
    story = []
    for line in (*CONTEXT_LINES, *regimen_lines, NEXT_FIELD_LINE):
        story.append(Paragraph(line, styles["Normal"]))
        story.append(Spacer(1, 4))
    document.build(story)
    return buffer.getvalue()

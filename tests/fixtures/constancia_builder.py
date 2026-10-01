"""In-memory constancia builder for the §7a régime-determinacy cases.

The committed `constancia_situacion_fiscal.pdf` is the *valid* golden fixture. §7a also
requires the same document with a deliberately undetermined régime — no occurrence, no
code, a non-3-digit code, no description, a description that runs into the next CSF
field, or two occurrences — so those bytes are built here rather than committed as
blobs. Every other field is known-good, which means a test built from here can only
fail on the régime decision it is about.

M2-E extends the same builder for the other two field-bounded facts: an optional postal
code and an optional obligation list, each placeable *before* another field so a test can
prove the value stops at its own field's boundary. The data lines are keyword arguments
with empty defaults, so the régime cases are unchanged; the RFC and persona type are
overridable for the same reason, so a constancia can be built for a generic-RFC
contributor without touching the committed fixture.

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

#: The RFC and persona type a constancia carries unless a test asks for others: the
#: values the committed golden fixture names. A test that needs a different contributor
#: (the two generic RFCs, say) passes them in, so the rest of the document stays known-good.
DEFAULT_RFC = "WATM640917J45"
DEFAULT_PERSONA = "Persona Física"


def _context_lines(rfc: str, persona: str) -> tuple[str, ...]:
    """Every field a parse needs to reach the régime: RFC, name and persona type.

    They are valid, so only the field under test can fail the parse.
    """
    return (
        "CONSTANCIA DE SITUACIÓN FISCAL",
        f"RFC: {rfc}",
        "Denominación o Razón Social: PRUEBA PERSONA FISICA",
        f"Persona: {persona}",
    )


#: The field that follows the régime in a constancia — the one an unbounded régime
#: description used to swallow (§7a: "description spilling into the next CSF field").
NEXT_FIELD_LINE = "Situación Fiscal: Activo"


def build_constancia_bytes(
    *regimen_lines: str,
    rfc: str = DEFAULT_RFC,
    persona: str = DEFAULT_PERSONA,
    codigo_postal_lines: tuple[str, ...] = (),
    obligaciones_lines: tuple[str, ...] = (),
    trailing_lines: tuple[str, ...] = (),
) -> bytes:
    """Build a one-page constancia whose régime field(s) are exactly `regimen_lines`.

    No line at all builds a constancia with no `Régimen Fiscal` field. One line builds
    the determinate/undetermined single-occurrence shape; two lines build the ambiguous
    double-occurrence shape. Lines are plain literal text (no reportlab markup).

    The field order is fixed and documented so a test can put a field *after* the one it
    is about: context, the régime lines, `Situación Fiscal`, then the postal code, the
    obligation list, and finally any `trailing_lines` (default: none, so a field is last).
    """
    styles = getSampleStyleSheet()
    buffer = io.BytesIO()
    document = SimpleDocTemplate(buffer, pagesize=letter)
    story = []
    lines = (
        *_context_lines(rfc, persona),
        *regimen_lines,
        NEXT_FIELD_LINE,
        *codigo_postal_lines,
        *obligaciones_lines,
        *trailing_lines,
    )
    for line in lines:
        story.append(Paragraph(line, styles["Normal"]))
        story.append(Spacer(1, 4))
    document.build(story)
    return buffer.getvalue()

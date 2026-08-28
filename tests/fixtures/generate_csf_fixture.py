"""Regenerate the synthetic-but-real CSF PDF golden fixture (M2.2).

Synthetic TEST data only (RFC taken from the SAT FIEL-de-pruebas package), no
real taxpayer information. The runtime PdfCsfParser reads a REAL constancia
PDF; this fixture exercises that extraction path deterministically.

Run:  python tests/fixtures/generate_csf_fixture.py
"""

from __future__ import annotations

from pathlib import Path

from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer

_OUT = Path(__file__).parent / "constancia_situacion_fiscal.pdf"

_LINES = [
    "CONSTANCIA DE SITUACIÓN FISCAL",
    "",
    "RFC: WATM640917J45",
    "CURP: WATM640917HNLMSB0A",
    "Denominación o Razón Social: PRUEBA PERSONA FISICA",
    "Persona: Persona Física",
    "Régimen Fiscal: 612 - Personas físicas con actividades empresariales y profesionales",
    "Actividad Económica: 1 - Siembra, cultivo y cosecha de soya",
    "Situación Fiscal: Activo",
    "Fecha Inicio de Operaciones: 2020-01-01",
    "Obligaciones: 3 Declarar anualmente el ISR; 33 Declarar mensualmente el ISR por actividades "
    "empresariales; 9 Declarar mensualmente el IVA.",
]


def _escape(text: str) -> str:
    return text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def build() -> None:
    styles = getSampleStyleSheet()
    doc = SimpleDocTemplate(str(_OUT), pagesize=letter)
    story = []
    for line in _LINES:
        story.append(Paragraph(_escape(line), styles["Normal"]))
        story.append(Spacer(1, 4))
    doc.build(story)
    print(f"wrote {_OUT}")


if __name__ == "__main__":
    build()

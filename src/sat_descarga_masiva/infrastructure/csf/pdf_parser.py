"""PdfCsfParser — extracts normalized CsfData from a real constancia PDF (M2.2)."""

from __future__ import annotations

import re
from datetime import date
from io import BytesIO

from pypdf import PdfReader

from sat_descarga_masiva.domain.errors import CsfParseError
from sat_descarga_masiva.domain.model.contributor import PersonaTipo, RegimenFiscal, SituacionFiscal
from sat_descarga_masiva.domain.model.csf import CsfData
from sat_descarga_masiva.domain.model.value_objects import Rfc

_RFC = re.compile(r"RFC:\s*([A-ZÑ&]{3,4}\d{6}[A-Z0-9]{3})", re.IGNORECASE)
_PERSONA = re.compile(r"Persona:\s*(Persona Física|Persona Moral)", re.IGNORECASE)
_SITUACION = re.compile(r"Situación Fiscal:\s*(.+)", re.IGNORECASE)
_NOMBRE = re.compile(r"(?:Denominación o Razón Social|Razón Social|Nombre):\s*(.+)", re.IGNORECASE)
_FECHA = re.compile(r"Fecha Inicio de Operaciones:\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE)

#: The régime field's label, counted over the whole text: §7a requires exactly one.
_REGIMEN_LABEL = re.compile(r"Régimen Fiscal:", re.IGNORECASE)

#: Where a CSF field starts: a label — an upper-case-initial token of at most 60
#: characters, closed by `:` — that does not continue a word. pypdf's output is not a
#: layout (its whitespace crosses line breaks), so an unbounded description swallows
#: whatever field follows it (§7a: "description spilling into the next CSF field").
_FIELD_LABEL = re.compile(r"(?<![\w])[A-ZÁÉÍÓÚÜÑ][^:\n]{0,60}:")

#: §7a's structural value: exactly three digits, a separator, then a description. It is
#: matched against a value already bounded to its field and flattened onto one line.
_REGIMEN_VALUE = re.compile(r"^(\d{3})\s*[-–]\s*")

_SITUACION_MAP = {
    "activo": SituacionFiscal.ACTIVO,
    "suspendido": SituacionFiscal.SUSPENDIDO,
    "cancelado": SituacionFiscal.CANCELADO,
}


class PdfCsfParser:
    """Implements application.ports.csf.CSFParser over a real PDF via pypdf."""

    def parse(self, pdf_bytes: bytes) -> CsfData:
        try:
            reader = PdfReader(BytesIO(pdf_bytes))
            text = "\n".join(page.extract_text() or "" for page in reader.pages)
        except Exception as exc:
            raise CsfParseError("constancia PDF could not be read") from exc
        return _to_csf_data(text)


def _to_csf_data(text: str) -> CsfData:
    rfc_match = _RFC.search(text)
    persona_match = _PERSONA.search(text)
    situacion_match = _SITUACION.search(text)
    if rfc_match is None:
        raise CsfParseError("RFC not found in constancia")
    if persona_match is None:
        raise CsfParseError("contributor type (Persona) not found in constancia")
    regimen_fiscal = _regimen_fiscal(text)
    if situacion_match is None:
        raise CsfParseError("Situación Fiscal not found in constancia")
    nombre_match = _NOMBRE.search(text)
    fecha_match = _FECHA.search(text)
    persona = persona_match.group(1).strip().lower()
    situacion = _SITUACION_MAP.get(situacion_match.group(1).strip().lower())
    if situacion is None:
        raise CsfParseError("unknown Situación Fiscal value")
    return CsfData(
        rfc=Rfc(rfc_match.group(1)),
        nombre=nombre_match.group(1).strip() if nombre_match else "",
        persona_tipo=PersonaTipo.FISICA if persona == "persona física" else PersonaTipo.MORAL,
        regimen_fiscal=regimen_fiscal,
        situacion_fiscal=situacion,
        fecha_inicio_operaciones=date.fromisoformat(fecha_match.group(1)) if fecha_match else None,
    )


def _regimen_fiscal(text: str) -> RegimenFiscal:
    """The single determinate régime occurrence of `text`, or a `CsfParseError` (§7a).

    "Determinate" is parsing confidence, not recognition: exactly one occurrence, one
    3-digit code, a non-empty description **bound to that occurrence** and no spillover
    into the next labelled CSF field. That confidence rule lives here because only the
    parser sees the raw extracted text — never in the profile, the resolver, the
    onboarding use case or persistence. An *unknown* code is still determinate: M2 keeps
    no SAT régimen catalog, so nothing here knows or cares whether a code exists.
    """
    ends = [match.end() for match in _REGIMEN_LABEL.finditer(text)]
    if not ends:
        raise CsfParseError("Régimen Fiscal not found in constancia")
    if len(ends) > 1:
        raise CsfParseError(
            f"Régimen Fiscal appears {len(ends)} times in constancia: "
            "which occurrence is current cannot be determined"
        )
    boundary = _FIELD_LABEL.search(text, ends[0])
    value = " ".join(text[ends[0] : None if boundary is None else boundary.start()].split())
    match = _REGIMEN_VALUE.match(value)
    if match is None:
        raise CsfParseError(
            f"Régimen Fiscal is not a 3-digit code followed by a description: {value!r}"
        )
    description = value[match.end() :].strip()
    if not description:
        raise CsfParseError("Régimen Fiscal description is empty")
    return RegimenFiscal(match.group(1), description)

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
_REGIMEN = re.compile(r"Régimen Fiscal:\s*(\d{3})\s*[-–]\s*(.+)", re.IGNORECASE)
_SITUACION = re.compile(r"Situación Fiscal:\s*(.+)", re.IGNORECASE)
_NOMBRE = re.compile(r"(?:Denominación o Razón Social|Razón Social|Nombre):\s*(.+)", re.IGNORECASE)
_FECHA = re.compile(r"Fecha Inicio de Operaciones:\s*(\d{4}-\d{2}-\d{2})", re.IGNORECASE)

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
    regimen_match = _REGIMEN.search(text)
    situacion_match = _SITUACION.search(text)
    if rfc_match is None:
        raise CsfParseError("RFC not found in constancia")
    if persona_match is None:
        raise CsfParseError("contributor type (Persona) not found in constancia")
    if regimen_match is None:
        raise CsfParseError("Régimen Fiscal not found in constancia")
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
        regimen_fiscal=RegimenFiscal(regimen_match.group(1), regimen_match.group(2).strip()),
        situacion_fiscal=situacion,
        fecha_inicio_operaciones=date.fromisoformat(fecha_match.group(1)) if fecha_match else None,
    )

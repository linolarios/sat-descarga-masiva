"""CsfData — normalized contributor info extracted from the CSF PDF (M2.2)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from sat_descarga_masiva.domain.model.contributor import (
    PersonaTipo,
    RegimenFiscal,
    SituacionFiscal,
)
from sat_descarga_masiva.domain.model.value_objects import Rfc


@dataclass(frozen=True)
class CsfData:
    """Normalized fiscal data extracted from constancia_situacion_fiscal.pdf."""

    rfc: Rfc
    nombre: str
    persona_tipo: PersonaTipo
    regimen_fiscal: RegimenFiscal
    situacion_fiscal: SituacionFiscal
    fecha_inicio_operaciones: date | None = None

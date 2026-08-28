"""Contributor fiscal identity (AGENT.md §7a). Domain-only, no I/O, no infra."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum

from sat_descarga_masiva.domain.model.value_objects import Rfc


class PersonaTipo(StrEnum):
    FISICA = "fisica"
    MORAL = "moral"


class SituacionFiscal(StrEnum):
    ACTIVO = "activo"
    SUSPENDIDO = "suspendido"
    CANCELADO = "cancelado"


@dataclass(frozen=True)
class RegimenFiscal:
    """SAT regimen fiscal carried verbatim from the CSF (code + description).

    Not a hand-enumerated catalog: the value comes from the constancia, so we
    do not invent regime classifications here.
    """

    code: str
    description: str


@dataclass(frozen=True)
class ContributorProfile:
    """Normalized contributor identity resolved from the CSF + FIEL cert."""

    rfc: Rfc
    nombre: str
    persona_tipo: PersonaTipo
    regimen_fiscal: RegimenFiscal
    situacion_fiscal: SituacionFiscal
    fecha_inicio_operaciones: date | None = None

"""Contributor fiscal identity (AGENT.md §7a). Domain-only, no I/O, no infra."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
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


@dataclass(frozen=True)
class ContributorProfileRecord:
    """A persisted, versioned profile snapshot with its CSF provenance (§7a).

    §7a lists ``csf_hash``/``csf_obtained_at``/``profile_version`` as profile
    fields and defines the audit chain "an accounting entry → `ContributorProfile`
    → `csf_hash` → original CSF". `ContributorProfile` itself stays the fiscal
    value; this record carries the provenance required to persist it, so a stored
    profile can never exist without the CSF it was resolved from.

    ``profile_version`` is the versioned-config counter of §7a ("extract only
    what parses confidently and require human confirmation (versioned config,
    `profile_version`)"): each version is immutable, so corrected extraction is a
    new version, never an in-place edit of a confirmed profile.
    """

    profile: ContributorProfile
    csf_hash: str
    csf_obtained_at: datetime
    profile_version: int
    recorded_at: datetime

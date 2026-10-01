"""Contributor fiscal identity (AGENT.md §7a). Domain-only, no I/O, no infra."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from types import MappingProxyType
from typing import Final

from sat_descarga_masiva.domain.model.value_objects import Rfc


class PersonaTipo(StrEnum):
    """The persona type §7a resolves for a contributor.

    `FISICA`/`MORAL` are what the constancia states. The two generic members are
    not CSF facts: they are the identity §7a names for the two generic RFCs, so
    they are only ever produced by the resolution step below (§7a), never read
    off a constancia and never inferred from an RFC's shape or length.
    """

    FISICA = "fisica"
    MORAL = "moral"
    GENERICO_NACIONAL = "generico_nacional"
    EXTRANJERO = "extranjero"


#: The two RFCs whose identity the constancia cannot answer (§7a). A closed,
#: explicit exception list — a mapping, not a rule: no other RFC's `PersonaTipo`
#: is derived from its length or pattern.
GENERIC_RFC_PERSONA: Final[Mapping[str, PersonaTipo]] = MappingProxyType(
    {
        "XAXX010101000": PersonaTipo.GENERICO_NACIONAL,
        "XEXX010101000": PersonaTipo.EXTRANJERO,
    }
)


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
class ObligacionFiscal:
    """One fiscal obligation exactly as the constancia states it (§7a).

    Opaque on purpose: `code` and `description` are carried verbatim, and nothing
    here knows what any code means. Recognition — which obligation feeds which
    downstream rule — belongs to the consumer that needs it, never to this value,
    so there is no catalog, no classification and no rewriting (D2).
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
    obligaciones: tuple[ObligacionFiscal, ...] = ()
    codigo_postal: str | None = None


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

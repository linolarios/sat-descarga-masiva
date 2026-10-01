"""CsfData — normalized contributor info extracted from the CSF PDF (M2.2)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from sat_descarga_masiva.domain.model.contributor import (
    ObligacionFiscal,
    PersonaTipo,
    RegimenFiscal,
    SituacionFiscal,
)
from sat_descarga_masiva.domain.model.value_objects import Rfc


@dataclass(frozen=True)
class CsfArtifact:
    """The stored CSF as an immutable source artifact (§7a).

    §7a: the CSF is "an **immutable source artifact**: hash it (`csf_hash`,
    SHA-256), retain the original, never modify it". ``stored_path`` is the
    location of the retained original; the record is keyed by its content hash,
    so the same constancia can never be repointed or quietly replaced.
    """

    sha256: str
    stored_path: str
    recorded_at: datetime


@dataclass(frozen=True)
class CsfData:
    """Normalized fiscal data extracted from constancia_situacion_fiscal.pdf.

    `obligaciones` is empty when the constancia carries no obligation list and
    `codigo_postal` is None when it carries no postal code: both are optional
    facts, and an absent fact is reported as absent rather than guessed (D3/D6).
    """

    rfc: Rfc
    nombre: str
    persona_tipo: PersonaTipo
    regimen_fiscal: RegimenFiscal
    situacion_fiscal: SituacionFiscal
    fecha_inicio_operaciones: date | None = None
    obligaciones: tuple[ObligacionFiscal, ...] = ()
    codigo_postal: str | None = None

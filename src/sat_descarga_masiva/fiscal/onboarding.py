"""Contributor onboarding: CsfData -> ContributorProfile + three-way RFC check (M2.2)."""

from __future__ import annotations

from dataclasses import dataclass

from sat_descarga_masiva.domain.model.contributor import GENERIC_RFC_PERSONA, ContributorProfile
from sat_descarga_masiva.domain.model.csf import CsfData
from sat_descarga_masiva.domain.model.value_objects import Rfc
from sat_descarga_masiva.fiscal.identity import RfcIdentityCheckResult, check_three_way_rfc


@dataclass(frozen=True)
class OnboardingResult:
    profile: ContributorProfile
    identity: RfcIdentityCheckResult


def resolve_contributor_profile(
    *, configured_rfc: Rfc, cert_rfc: Rfc, csf: CsfData
) -> OnboardingResult:
    """Resolve the profile: the constancia's facts, plus the generic-RFC identity exception.

    The constancia decides `persona_tipo` for every ordinary RFC. The two generic
    RFCs cannot be described by a constancia, so §7a names their identity here —
    at resolution, where identity is decided — and the parsed `csf.persona_tipo`
    is left exactly as parsed (D8).
    """
    profile = ContributorProfile(
        rfc=csf.rfc,
        nombre=csf.nombre,
        persona_tipo=GENERIC_RFC_PERSONA.get(csf.rfc.value, csf.persona_tipo),
        regimen_fiscal=csf.regimen_fiscal,
        situacion_fiscal=csf.situacion_fiscal,
        fecha_inicio_operaciones=csf.fecha_inicio_operaciones,
        obligaciones=csf.obligaciones,
        codigo_postal=csf.codigo_postal,
    )
    identity = check_three_way_rfc(configured=configured_rfc, cert=cert_rfc, csf=csf.rfc)
    return OnboardingResult(profile=profile, identity=identity)

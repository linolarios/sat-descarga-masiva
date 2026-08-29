"""Contributor onboarding: CsfData -> ContributorProfile + three-way RFC check (M2.2)."""

from __future__ import annotations

from dataclasses import dataclass

from sat_descarga_masiva.domain.model.contributor import ContributorProfile
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
    profile = ContributorProfile(
        rfc=csf.rfc,
        nombre=csf.nombre,
        persona_tipo=csf.persona_tipo,
        regimen_fiscal=csf.regimen_fiscal,
        situacion_fiscal=csf.situacion_fiscal,
        fecha_inicio_operaciones=csf.fecha_inicio_operaciones,
    )
    identity = check_three_way_rfc(configured=configured_rfc, cert=cert_rfc, csf=csf.rfc)
    return OnboardingResult(profile=profile, identity=identity)

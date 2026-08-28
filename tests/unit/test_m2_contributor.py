"""M2.1: ContributorProfile + static three-way RFC identity check (§7a)."""

from datetime import date

from sat_descarga_masiva.domain.model.contributor import (
    ContributorProfile,
    PersonaTipo,
    RegimenFiscal,
    SituacionFiscal,
)
from sat_descarga_masiva.domain.model.value_objects import Rfc
from sat_descarga_masiva.fiscal.identity import check_three_way_rfc

RFC = Rfc("AAA010101AAA")


def test_contributor_profile_holds_fiscal_fields() -> None:
    profile = ContributorProfile(
        rfc=RFC,
        nombre="EMPRESA PRUEBA SA DE CV",
        persona_tipo=PersonaTipo.MORAL,
        regimen_fiscal=RegimenFiscal("601", "General de Ley Personas Morales"),
        situacion_fiscal=SituacionFiscal.ACTIVO,
        fecha_inicio_operaciones=date(2020, 1, 1),
    )
    assert profile.rfc == RFC
    assert profile.persona_tipo is PersonaTipo.MORAL
    assert profile.regimen_fiscal.code == "601"
    assert profile.situacion_fiscal is SituacionFiscal.ACTIVO


def test_persona_tipo_values() -> None:
    assert PersonaTipo.FISICA.value == "fisica"
    assert PersonaTipo.MORAL.value == "moral"


def test_three_way_rfc_all_match() -> None:
    result = check_three_way_rfc(configured=RFC, cert=RFC, csf=RFC)
    assert result.matches is True
    assert result.mismatched_pairs == ()


def test_three_way_rfc_cert_mismatch() -> None:
    result = check_three_way_rfc(
        configured=RFC,
        cert=Rfc("BBB010101BBB"),
        csf=RFC,
    )
    assert result.matches is False
    assert result.mismatched_pairs == ("configured-vs-cert", "cert-vs-csf")


def test_three_way_rfc_csf_mismatch() -> None:
    result = check_three_way_rfc(
        configured=RFC,
        cert=RFC,
        csf=Rfc("CCC010101CCC"),
    )
    assert result.matches is False
    assert result.mismatched_pairs == ("configured-vs-csf", "cert-vs-csf")


def test_three_way_rfc_all_different() -> None:
    result = check_three_way_rfc(
        configured=Rfc("AAA010101AAA"),
        cert=Rfc("BBB010101BBB"),
        csf=Rfc("CCC010101CCC"),
    )
    assert result.matches is False
    assert len(result.mismatched_pairs) == 3

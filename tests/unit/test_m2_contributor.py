"""M2.1: ContributorProfile + static three-way RFC identity check (§7a).

M2-E adds the two generic RFCs §7a names as identity exceptions: `PersonaTipo`
gains `GENERICO_NACIONAL`/`EXTRANJERO` and `GENERIC_RFC_PERSONA` records which
RFC answers to which. It is a mapping, not an inference rule: nothing here
derives a persona type from an RFC's shape or length.
"""

from dataclasses import replace
from datetime import date

from sat_descarga_masiva.domain.model.contributor import (
    GENERIC_RFC_PERSONA,
    ContributorProfile,
    ObligacionFiscal,
    PersonaTipo,
    RegimenFiscal,
    SituacionFiscal,
)
from sat_descarga_masiva.domain.model.value_objects import Rfc
from sat_descarga_masiva.fiscal.identity import check_three_way_rfc

RFC = Rfc("AAA010101AAA")


def _profile() -> ContributorProfile:
    return ContributorProfile(
        rfc=RFC,
        nombre="EMPRESA PRUEBA SA DE CV",
        persona_tipo=PersonaTipo.MORAL,
        regimen_fiscal=RegimenFiscal("601", "General de Ley Personas Morales"),
        situacion_fiscal=SituacionFiscal.ACTIVO,
        fecha_inicio_operaciones=date(2020, 1, 1),
    )


def test_contributor_profile_holds_fiscal_fields() -> None:
    profile = _profile()
    assert profile.rfc == RFC
    assert profile.persona_tipo is PersonaTipo.MORAL
    assert profile.regimen_fiscal.code == "601"
    assert profile.situacion_fiscal is SituacionFiscal.ACTIVO


def test_persona_tipo_values() -> None:
    assert PersonaTipo.FISICA.value == "fisica"
    assert PersonaTipo.MORAL.value == "moral"
    assert PersonaTipo.GENERICO_NACIONAL.value == "generico_nacional"
    assert PersonaTipo.EXTRANJERO.value == "extranjero"


def test_only_the_two_generic_rfcs_have_a_pinned_persona_tipo() -> None:
    """§7a: exactly two RFCs are generic identities, and they are the generic ones."""
    assert dict(GENERIC_RFC_PERSONA) == {
        "XAXX010101000": PersonaTipo.GENERICO_NACIONAL,
        "XEXX010101000": PersonaTipo.EXTRANJERO,
    }


def test_obligacion_fiscal_is_an_opaque_two_field_value() -> None:
    """No catalog and no classification: the CSF's own code and wording, nothing else."""
    unknown = ObligacionFiscal(code="999", description="Obligación inventada")
    assert (unknown.code, unknown.description) == ("999", "Obligación inventada")
    assert set(ObligacionFiscal.__dataclass_fields__) == {"code", "description"}


def test_profile_holds_the_obligations_and_postal_code_the_csf_stated() -> None:
    obligaciones = (
        ObligacionFiscal("3", "Declarar anualmente el ISR"),
        ObligacionFiscal("9", "Declarar mensualmente el IVA."),
    )
    profile = replace(_profile(), obligaciones=obligaciones, codigo_postal="97000")
    assert profile.obligaciones == obligaciones
    assert profile.codigo_postal == "97000"


def test_profile_defaults_to_no_obligations_and_no_postal_code() -> None:
    """Both are optional CSF facts: absent stays absent, never a fabricated value."""
    profile = _profile()
    assert profile.obligaciones == ()
    assert profile.codigo_postal is None


def test_profile_equality_covers_obligations_and_postal_code() -> None:
    """`_unchanged` compares profiles structurally, so the new facts must be part of it."""
    base = _profile()
    assert base != replace(base, obligaciones=(ObligacionFiscal("3", "Declarar el ISR"),))
    assert base != replace(base, codigo_postal="97000")


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

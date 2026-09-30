"""M2.2: PdfCsfParser (real PDF -> CsfData) + resolve_contributor_profile."""

from pathlib import Path

import pytest

from fixtures.constancia_builder import build_constancia_bytes
from sat_descarga_masiva.domain.errors import CsfParseError
from sat_descarga_masiva.domain.model.contributor import (
    PersonaTipo,
    RegimenFiscal,
    SituacionFiscal,
)
from sat_descarga_masiva.domain.model.value_objects import Rfc
from sat_descarga_masiva.fiscal.onboarding import resolve_contributor_profile
from sat_descarga_masiva.infrastructure.csf.pdf_parser import PdfCsfParser

_FIXTURE = Path(__file__).parent.parent / "fixtures" / "constancia_situacion_fiscal.pdf"


def _pdf_bytes() -> bytes:
    return _FIXTURE.read_bytes()


def test_parser_extracts_contributor_type_from_real_pdf() -> None:
    csf = PdfCsfParser().parse(_pdf_bytes())
    assert csf.rfc == Rfc("WATM640917J45")
    assert csf.persona_tipo is PersonaTipo.FISICA
    assert csf.regimen_fiscal.code == "612"
    assert csf.situacion_fiscal is SituacionFiscal.ACTIVO
    assert csf.nombre == "PRUEBA PERSONA FISICA"


def test_parser_raises_on_non_pdf_bytes() -> None:
    with pytest.raises(CsfParseError):
        PdfCsfParser().parse(b"definitely not a pdf")


def test_parser_raises_when_persona_missing(tmp_path: Path) -> None:
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import getSampleStyleSheet
    from reportlab.platypus import Paragraph, SimpleDocTemplate

    out = tmp_path / "no_persona.pdf"
    doc = SimpleDocTemplate(str(out), pagesize=letter)
    doc.build([Paragraph("RFC: WATM640917J45", getSampleStyleSheet()["Normal"])])
    with pytest.raises(CsfParseError):
        PdfCsfParser().parse(out.read_bytes())


def test_resolve_contributor_profile_matching() -> None:
    csf = PdfCsfParser().parse(_pdf_bytes())
    result = resolve_contributor_profile(
        configured_rfc=Rfc("WATM640917J45"), cert_rfc=Rfc("WATM640917J45"), csf=csf
    )
    assert result.profile.rfc == csf.rfc
    assert result.profile.persona_tipo is PersonaTipo.FISICA
    assert result.identity.matches is True


def test_resolve_contributor_profile_cert_mismatch() -> None:
    csf = PdfCsfParser().parse(_pdf_bytes())
    result = resolve_contributor_profile(
        configured_rfc=Rfc("WATM640917J45"), cert_rfc=Rfc("CACX7605101P8"), csf=csf
    )
    assert result.profile.rfc == csf.rfc
    assert result.identity.matches is False


# --- §7a: "determinate" is parsing confidence, not recognition -----------------
#
# Exactly one structurally valid occurrence — one 3-digit code plus a non-empty
# description bound to that occurrence — is what makes a régime determinate. Anything
# else is a `CsfParseError` here, in the parser, because only the parser has the raw
# extracted text. An *unknown* code stays acceptable: M2 keeps no SAT régime catalog.


def test_the_committed_constancia_carries_one_determinate_regimen() -> None:
    """Case 1: the golden fixture's régime survives the confidence rule unchanged."""
    csf = PdfCsfParser().parse(_pdf_bytes())
    assert csf.regimen_fiscal == RegimenFiscal(
        "612", "Personas físicas con actividades empresariales y profesionales"
    )


def test_parser_accepts_an_unknown_but_structurally_clear_regimen() -> None:
    """Case 2: `999` is determinate — the guard against a hidden SAT whitelist."""
    csf = PdfCsfParser().parse(build_constancia_bytes("Régimen Fiscal: 999 - Foo Bar Desconocido"))
    assert csf.regimen_fiscal.code == "999"
    assert csf.regimen_fiscal.description == "Foo Bar Desconocido"


def test_parser_raises_when_the_regimen_field_is_absent() -> None:
    """Case 3: zero occurrences ⇒ `CsfParseError`."""
    with pytest.raises(CsfParseError, match="not found"):
        PdfCsfParser().parse(build_constancia_bytes())


@pytest.mark.parametrize(
    "line",
    ["Régimen Fiscal:", "Régimen Fiscal: Foo Bar", "Régimen Fiscal:     "],
)
def test_parser_raises_when_the_regimen_has_no_code(line: str) -> None:
    """Case 4: no code at all ⇒ `CsfParseError`."""
    with pytest.raises(CsfParseError):
        PdfCsfParser().parse(build_constancia_bytes(line))


@pytest.mark.parametrize(
    "line",
    [
        "Régimen Fiscal: 61 - Foo",
        "Régimen Fiscal: 0612 - Foo",
        "Régimen Fiscal: 6123 - Foo",
        "Régimen Fiscal: ABC - Foo",
    ],
)
def test_parser_raises_when_the_regimen_code_is_not_exactly_three_digits(line: str) -> None:
    """Case 5: the structural rule is exactly three digits — not a known SAT code."""
    with pytest.raises(CsfParseError):
        PdfCsfParser().parse(build_constancia_bytes(line))


@pytest.mark.parametrize(
    "line",
    ["Régimen Fiscal: 612", "Régimen Fiscal: 612 -", "Régimen Fiscal: 612 -    "],
)
def test_parser_raises_when_the_regimen_description_is_missing(line: str) -> None:
    """Cases 6/7: a code is not enough — the description must be non-empty."""
    with pytest.raises(CsfParseError):
        PdfCsfParser().parse(build_constancia_bytes(line))


def test_regimen_without_description_never_consumes_the_next_csf_field() -> None:
    """Case 8: pypdf's text is not a layout, so `\\s` crosses a newline.

    `Régimen Fiscal: 999 -` followed by `Situación Fiscal: Activo` used to parse as
    `description="Situación Fiscal: Activo"`: the separator's trailing `\\s*` ate the
    line break and `(.+)` took the next field's text. The description must stay bound
    to the occurrence it belongs to.
    """
    with pytest.raises(CsfParseError):
        PdfCsfParser().parse(build_constancia_bytes("Régimen Fiscal: 999 -"))


def test_parser_raises_when_the_regimen_appears_more_than_once() -> None:
    """Case 9: exactly one occurrence — never the first, the last, or a merge."""
    with pytest.raises(CsfParseError, match="Régimen Fiscal"):
        PdfCsfParser().parse(
            build_constancia_bytes(
                "Régimen Fiscal: 601 - General de Ley Personas Morales",
                "Régimen Fiscal: 612 - Personas físicas con actividades empresariales",
            )
        )

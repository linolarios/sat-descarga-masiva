"""M2.2: PdfCsfParser (real PDF -> CsfData) + resolve_contributor_profile."""

from pathlib import Path

import pytest

from fixtures.constancia_builder import build_constancia_bytes
from sat_descarga_masiva.domain.errors import CsfParseError
from sat_descarga_masiva.domain.model.contributor import (
    ObligacionFiscal,
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


# --- M2-E: obligations and postal code are field-bounded CSF facts -----------------
#
# Both are optional: an absent field is reported as absent (`()` / `None`). A field
# that *is* present must be readable in full — a present obligation list holding a
# malformed item, and a present-but-empty postal code, are `CsfParseError`s rather
# than partial results. Nothing here classifies an obligation or reads postal-code
# semantics: the values are carried verbatim, and the same `_FIELD_LABEL` boundary
# that made the régime determinate keeps each value inside its own field.

_VALID_REGIMEN = "Régimen Fiscal: 612 - Personas físicas con actividades empresariales"


def _with_obligations(*lines: str, after: tuple[str, ...] = ()) -> bytes:
    return build_constancia_bytes(_VALID_REGIMEN, obligaciones_lines=lines, trailing_lines=after)


def _with_codigo_postal(*lines: str, obligaciones: tuple[str, ...] = ()) -> bytes:
    return build_constancia_bytes(
        _VALID_REGIMEN, codigo_postal_lines=lines, obligaciones_lines=obligaciones
    )


def test_the_committed_constancia_carries_its_three_obligations() -> None:
    """O1: the golden fixture's obligation list, read from the real PDF, in source order."""
    csf = PdfCsfParser().parse(_pdf_bytes())
    assert csf.obligaciones == (
        ObligacionFiscal("3", "Declarar anualmente el ISR"),
        ObligacionFiscal("33", "Declarar mensualmente el ISR por actividades empresariales"),
        ObligacionFiscal("9", "Declarar mensualmente el IVA."),
    )


def test_parser_reports_no_obligations_when_the_field_is_absent() -> None:
    """O2: an absent obligation list is `()` — the constancia is simply silent about it."""
    csf = PdfCsfParser().parse(build_constancia_bytes(_VALID_REGIMEN))
    assert csf.obligaciones == ()


def test_a_single_obligation_is_carried_verbatim() -> None:
    """O3: code and description exactly as written, including the field-final period."""
    csf = PdfCsfParser().parse(_with_obligations("Obligaciones: 9 Declarar mensualmente el IVA."))
    assert csf.obligaciones == (ObligacionFiscal("9", "Declarar mensualmente el IVA."),)


def test_obligations_keep_their_source_order_and_are_never_sorted() -> None:
    """O4: the list is a fact in the constancia's order, not a set to be reordered."""
    csf = PdfCsfParser().parse(
        _with_obligations(
            "Obligaciones: 9 Declarar el IVA; 3 Declarar el ISR anual; 33 Declarar el ISR mensual"
        )
    )
    assert [obligacion.code for obligacion in csf.obligaciones] == ["9", "3", "33"]
    assert csf.obligaciones[0].description == "Declarar el IVA"


def test_a_wrapped_obligation_is_one_item_on_one_line() -> None:
    """O5: pypdf's text is not a layout, so a wrapped item must be reassembled, not split."""
    csf = PdfCsfParser().parse(
        _with_obligations(
            "Obligaciones: 3 Declarar anualmente el ISR; 33 Declarar mensualmente",
            "el ISR por actividades empresariales; 9 Declarar mensualmente el IVA.",
        )
    )
    assert csf.obligaciones == (
        ObligacionFiscal("3", "Declarar anualmente el ISR"),
        ObligacionFiscal("33", "Declarar mensualmente el ISR por actividades empresariales"),
        ObligacionFiscal("9", "Declarar mensualmente el IVA."),
    )


def test_parser_raises_on_a_malformed_obligation_item() -> None:
    """O6: one unreadable item fails the whole field — never a silently shortened list."""
    with pytest.raises(CsfParseError, match="Obligaciones"):
        PdfCsfParser().parse(
            _with_obligations("Obligaciones: 3 Declarar el ISR; BROKEN SEGMENT; 9 Declarar el IVA")
        )


def test_parser_raises_when_an_obligation_has_no_code() -> None:
    """O7: a description without its code is not an obligation we can carry."""
    with pytest.raises(CsfParseError, match="Obligaciones"):
        PdfCsfParser().parse(_with_obligations("Obligaciones: Declarar el IVA"))


def test_parser_raises_when_an_obligation_has_no_description() -> None:
    """O8: a code alone is not an obligation either."""
    with pytest.raises(CsfParseError, match="Obligaciones"):
        PdfCsfParser().parse(_with_obligations("Obligaciones: 9 "))


def test_parser_raises_when_the_obligations_field_is_empty() -> None:
    """O8b: present but empty is a `CsfParseError`, like every other present CSF field."""
    with pytest.raises(CsfParseError, match="Obligaciones"):
        PdfCsfParser().parse(_with_obligations("Obligaciones:"))


def test_a_single_trailing_separator_is_not_an_extra_item() -> None:
    """O11: the field-final `;` is a separator, so exactly one empty segment is dropped."""
    csf = PdfCsfParser().parse(
        _with_obligations("Obligaciones: 3 Declarar el ISR; 9 Declarar el IVA;")
    )
    assert [obligacion.code for obligacion in csf.obligaciones] == ["3", "9"]


@pytest.mark.parametrize(
    "line",
    [
        "Obligaciones: 3 Declarar el ISR;; 9 Declarar el IVA",  # interior empty segment
        "Obligaciones: 3 Declarar el ISR; 9 Declarar el IVA;;",  # a second trailing empty
    ],
)
def test_parser_raises_on_any_empty_obligation_segment(line: str) -> None:
    """O12: only the single separator-generated empty segment is ignored — the rest fail."""
    with pytest.raises(CsfParseError, match="Obligaciones"):
        PdfCsfParser().parse(_with_obligations(line))


def test_parser_raises_when_obligations_appear_more_than_once() -> None:
    """O13: two occurrences are undetermined, exactly as for the régime (§7a)."""
    with pytest.raises(CsfParseError, match="Obligaciones"):
        PdfCsfParser().parse(
            _with_obligations(
                "Obligaciones: 3 Declarar el ISR",
                "Obligaciones: 9 Declarar el IVA",
            )
        )


def test_obligations_never_consume_the_field_that_follows() -> None:
    """O9: the value stops at the next labelled field, so the next field stays intact."""
    csf = PdfCsfParser().parse(
        _with_obligations(
            "Obligaciones: 3 Declarar anualmente el ISR",
            after=("Código Postal: 97000",),
        )
    )
    assert csf.obligaciones == (ObligacionFiscal("3", "Declarar anualmente el ISR"),)
    assert csf.codigo_postal == "97000"


def test_an_unknown_but_clear_obligation_code_is_accepted() -> None:
    """O10: no catalog — an obligation code we have never seen is still an obligation."""
    csf = PdfCsfParser().parse(_with_obligations("Obligaciones: 999 Obligación inventada"))
    assert csf.obligaciones == (ObligacionFiscal("999", "Obligación inventada"),)


# --- M2-E: the postal code is an optional, verbatim CSF fact ----------------------


def test_parser_reports_no_postal_code_when_the_field_is_absent() -> None:
    """CP1: absent postal code is None, never an empty string or a guess."""
    assert PdfCsfParser().parse(build_constancia_bytes(_VALID_REGIMEN)).codigo_postal is None


def test_the_postal_code_is_carried_verbatim() -> None:
    """CP2: the printed value, with no postal-code semantics attached to it."""
    csf = PdfCsfParser().parse(_with_codigo_postal("Código Postal: 97000"))
    assert csf.codigo_postal == "97000"


def test_the_postal_code_never_consumes_the_field_that_follows() -> None:
    """CP3: bounded by the same `_FIELD_LABEL`, so the next field is untouched."""
    csf = PdfCsfParser().parse(
        _with_codigo_postal(
            "Código Postal: 97000",
            obligaciones=("Obligaciones: 9 Declarar mensualmente el IVA.",),
        )
    )
    assert csf.codigo_postal == "97000"
    assert csf.obligaciones == (ObligacionFiscal("9", "Declarar mensualmente el IVA."),)


def test_parser_raises_when_the_postal_code_is_empty() -> None:
    """CP4: a present field with no value is not a value."""
    with pytest.raises(CsfParseError, match="Código Postal"):
        PdfCsfParser().parse(_with_codigo_postal("Código Postal:"))


def test_parser_raises_when_the_postal_code_appears_more_than_once() -> None:
    """CP5: two occurrences are undetermined, exactly as for the régime (§7a)."""
    with pytest.raises(CsfParseError, match="Código Postal"):
        PdfCsfParser().parse(_with_codigo_postal("Código Postal: 97000", "Código Postal: 97001"))

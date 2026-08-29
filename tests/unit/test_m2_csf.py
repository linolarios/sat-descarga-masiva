"""M2.2: PdfCsfParser (real PDF -> CsfData) + resolve_contributor_profile."""

from pathlib import Path

import pytest

from sat_descarga_masiva.domain.errors import CsfParseError
from sat_descarga_masiva.domain.model.contributor import PersonaTipo, SituacionFiscal
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

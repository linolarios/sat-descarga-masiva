from pathlib import Path

from sat_descarga_masiva.domain.enums.request_state import RequestState
from sat_descarga_masiva.infrastructure.sat.xml.parsers import parse_verification

FIXTURES = Path(__file__).parent.parent / "fixtures"
FIXTURE = FIXTURES / "verifica_response.xml"
FIXTURE_EMPTY = FIXTURES / "verifica_response_empty.xml"

# The same completed response with no IdsPaquetes element at all — the shape a real
# "completed, nothing found" response takes when the list is omitted instead of emptied.
NO_PACKAGE_ELEMENT = b"""<s:Envelope xmlns:s="http://schemas.xmlsoap.org/soap/envelope/">
 <s:Body><VerificaSolicitudDescargaResponse xmlns="http://DescargaMasivaTerceros.sat.gob.mx">
  <VerificaSolicitudDescargaResult CodEstatus="5000" EstadoSolicitud="3"
    CodigoEstadoSolicitud="5000" NumeroCFDIs="0" Mensaje="Solicitud Aceptada"/>
 </VerificaSolicitudDescargaResponse></s:Body>
</s:Envelope>"""


def test_parses_spec_response() -> None:
    result = parse_verification(FIXTURE.read_bytes())
    assert result.state is RequestState.COMPLETED
    assert result.cod_estatus.value == "5000"
    assert len(result.ids_paquetes) == 6


def test_completed_response_with_an_empty_package_list_has_no_packages() -> None:
    """ "Nothing to download yet" is a completed result with zero packages, not an error."""
    result = parse_verification(FIXTURE_EMPTY.read_bytes())
    assert result.state is RequestState.COMPLETED
    assert result.cod_estatus.value == "5000"
    assert result.numero_cfdis == 0
    assert result.ids_paquetes == ()


def test_completed_response_without_the_package_element_has_no_packages() -> None:
    result = parse_verification(NO_PACKAGE_ELEMENT)
    assert result.state is RequestState.COMPLETED
    assert result.ids_paquetes == ()

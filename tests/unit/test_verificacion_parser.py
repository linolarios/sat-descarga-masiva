from pathlib import Path

from sat_descarga_masiva.domain.enums.request_state import RequestState
from sat_descarga_masiva.infrastructure.sat.xml.parsers import parse_verification

FIXTURE = Path(__file__).parent.parent / "fixtures" / "verifica_response.xml"


def test_parses_spec_response() -> None:
    result = parse_verification(FIXTURE.read_bytes())
    assert result.state is RequestState.COMPLETED
    assert result.cod_estatus.value == "5000"
    assert len(result.ids_paquetes) == 6

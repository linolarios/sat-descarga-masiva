from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent.parent / "fixtures" / "verifica_response.xml"


@pytest.mark.xfail(
    reason="TDD: implement infrastructure.sat.xml.parsers.parse_verification", strict=False
)
def test_parses_spec_response() -> None:
    from sat_descarga_masiva.domain.enums.request_state import RequestState
    from sat_descarga_masiva.infrastructure.sat.xml.parsers import (
        parse_verification,  # implement me
    )

    result = parse_verification(FIXTURE.read_bytes())
    assert result.state is RequestState.COMPLETED
    assert result.cod_estatus.value == "5000"
    assert len(result.ids_paquetes) == 6

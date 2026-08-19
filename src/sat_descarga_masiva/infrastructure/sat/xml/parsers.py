"""Anti-Corruption Layer: SAT SOAP/XML bytes -> typed domain values.

Only the typed domain model (``VerificationResult`` et al.) crosses the
``infrastructure.sat`` boundary; XML parsing and lxml stay in this layer.
"""

from __future__ import annotations

from lxml import etree  # type: ignore[import-untyped]  # no stubs installed

from sat_descarga_masiva.domain.enums.request_state import RequestState
from sat_descarga_masiva.domain.enums.sat_status import SatStatusCode
from sat_descarga_masiva.domain.model.results import VerificationResult
from sat_descarga_masiva.domain.model.value_objects import PackageId

_RESULT_LOCAL_NAME = "VerificaSolicitudDescargaResult"
_PACKAGE_LOCAL_NAME = "IdsPaquetes"


def parse_verification(xml: bytes) -> VerificationResult:
    """Parse a VerificaSolicitudDescarga SOAP response into a domain result.

    Locates elements by local name so the SOAP envelope and its namespace
    declarations need not be hard-coded here.
    """
    root = etree.fromstring(xml)
    result = root.xpath(f".//*[local-name()='{_RESULT_LOCAL_NAME}']")[0]

    state = RequestState(int(result.get("EstadoSolicitud")))
    cod_estatus = SatStatusCode(result.get("CodigoEstadoSolicitud"))
    numero_cfdis = int(result.get("NumeroCFDIs") or 0)
    mensaje = result.get("Mensaje") or ""
    package_ids = tuple(
        PackageId(token.strip())
        for token in result.xpath(f".//*[local-name()='{_PACKAGE_LOCAL_NAME}']/text()")
    )

    return VerificationResult(
        state=state,
        cod_estatus=cod_estatus,
        numero_cfdis=numero_cfdis,
        mensaje=mensaje,
        ids_paquetes=package_ids,
    )

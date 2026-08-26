"""SatcfdiGateway — implements the SatGateway application port over the pinned satcfdi.

This adapter is an Anti-Corruption Layer: it translates satcfdi DTOs/enums into
our own domain types and NEVER lets a satcfdi type, enum, exception, or XML
element cross the application boundary (AGENT.md §5). It is transport-only:
no cursor, retry, partition, persistence or accounting lives here.

The adapter talks to a `satcfdi.pacs.sat.SAT` instance. Tests inject a fake SAT
double; composition wires the real, pinned SAT (AGENT.md §5 NON-NORMATIVE →
verified against 26.8.0 at build time).
"""

from __future__ import annotations

import base64
from datetime import timedelta

from satcfdi.pacs.sat import (  # type: ignore[import-untyped]
    SAT,
    EstadoComprobante,
    TipoDescargaMasivaTerceros,
)

from sat_descarga_masiva.application.ports.gateways import SatGateway
from sat_descarga_masiva.application.ports.services import Clock
from sat_descarga_masiva.domain.enums.catalog import Direction, DocumentStatus, RequestType
from sat_descarga_masiva.domain.enums.request_state import RequestState
from sat_descarga_masiva.domain.enums.sat_status import SatStatusCode
from sat_descarga_masiva.domain.model.credentials import SigningIdentity
from sat_descarga_masiva.domain.model.query import DownloadQuery
from sat_descarga_masiva.domain.model.results import Package, SubmitResult, VerificationResult
from sat_descarga_masiva.domain.model.token import AccessToken
from sat_descarga_masiva.domain.model.value_objects import PackageId, RequestId, Rfc

# satcfdi's EstadoSolicitud IntEnum (1..6) maps 1:1 onto our RequestState.
_REQUEST_STATE = {
    1: RequestState.ACCEPTED,
    2: RequestState.PROCESSING,
    3: RequestState.COMPLETED,
    4: RequestState.ERROR,
    5: RequestState.REJECTED,
    6: RequestState.EXPIRED,
}

# Our RequestType / DocumentStatus → satcfdi enums. Kept here; satcfdi never
# reaches the application.
_TIPO_SOLICITUD = {
    RequestType.CFDI: TipoDescargaMasivaTerceros.CFDI,
    RequestType.METADATA: TipoDescargaMasivaTerceros.METADATA,
}
_ESTADO_COMPROBANTE = {
    DocumentStatus.TODOS: EstadoComprobante.TODOS,
    DocumentStatus.VIGENTE: EstadoComprobante.VIGENTE,
    DocumentStatus.CANCELADO: EstadoComprobante.CANCELADO,
}

_TOKEN_TTL = timedelta(minutes=30)


class SatcfdiGateway:
    """Implements SatGateway over `satcfdi.pacs.sat.SAT` (verified against 26.8.0)."""

    def __init__(self, sat: SAT, signer: SigningIdentity, clock: Clock) -> None:
        self._sat = sat
        self._signer = signer
        self._clock = clock
        # The signer's RFC is authoritative for the contributor.
        self._rfc = signer.rfc.upper()

    def authenticate(self, identity: SigningIdentity) -> AccessToken:
        # satcfdi v26.8 manages its SOAP token on the SAT instance; we mint a
        # repr-safe AccessToken so the application flow stays uniform. The raw
        # token value stays internal to the adapter state.
        now = self._clock.now()
        return AccessToken(
            value=f"satcfdi:{self._rfc}",
            created_at=now,
            expires_at=now + _TOKEN_TTL,
        )

    def request(self, query: DownloadQuery, token: AccessToken) -> SubmitResult:
        # The signer's RFC is the taxpayer: EMITIDOS -> emisor, RECIBIDOS -> receptor.
        if query.direction is Direction.EMITIDOS:
            response = self._sat.recover_comprobante_emitted_request(
                fecha_inicial=query.date_range.start,
                fecha_final=query.date_range.end,
                rfc_emisor=query.rfc_solicitante.value,
                rfc_receptor=query.rfc_receptor.value if query.rfc_receptor is not None else None,
                tipo_solicitud=_TIPO_SOLICITUD[query.request_type],
                estado_comprobante=_ESTADO_COMPROBANTE[query.document_status],
            )
            return self._to_submit_result(response)
        response = self._sat.recover_comprobante_received_request(
            fecha_inicial=query.date_range.start,
            fecha_final=query.date_range.end,
            rfc_receptor=query.rfc_solicitante.value,
            rfc_emisor=query.rfc_emisor.value if query.rfc_emisor is not None else None,
            tipo_solicitud=_TIPO_SOLICITUD[query.request_type],
            estado_comprobante=_ESTADO_COMPROBANTE[query.document_status],
        )
        return self._to_submit_result(response)

    def verify(self, request_id: RequestId, rfc: Rfc, token: AccessToken) -> VerificationResult:
        response = self._sat.recover_comprobante_status(request_id.value)
        return VerificationResult(
            state=_REQUEST_STATE[int(response["EstadoSolicitud"])],
            cod_estatus=SatStatusCode(response["CodEstatus"]),
            numero_cfdis=int(response.get("NumeroCFDIs", 0)),
            mensaje=response.get("Mensaje", ""),
            ids_paquetes=tuple(PackageId(p) for p in response.get("IdsPaquetes", [])),
        )

    def download(self, package_id: PackageId, rfc: Rfc, token: AccessToken) -> Package:
        _response, package_text = self._sat.recover_comprobante_download(package_id.value)
        # satcfdi returns the package as base64 text; decode to raw zip bytes.
        content = base64.b64decode(package_text)
        return Package(package_id=package_id, content=content)

    def _to_submit_result(self, response: dict[str, object]) -> SubmitResult:
        cod = response.get("CodEstatus")
        rid = response.get("IdSolicitud")
        mensaje = response.get("Mensaje")
        return SubmitResult(
            request_id=RequestId(rid) if isinstance(rid, str) else None,
            cod_estatus=SatStatusCode(cod) if isinstance(cod, str) else SatStatusCode("5000"),
            mensaje=mensaje if isinstance(mensaje, str) else "",
        )


def _conforms(g: SatcfdiGateway) -> SatGateway:
    """mypy proof that SatcfdiGateway satisfies the SatGateway Protocol (§12)."""
    return g

"""SatGateway adapter contract (AGENT.md §12): proves swappability.

The same behavior assertions run against every SAT-gateway implementation.
Currently only the FakeSatGateway double exists; SatcfdiGateway is added to
GATEWAY_FACTORIES when M1 lands it — the suite then proves interchangeability.
"""

from __future__ import annotations

import base64
from datetime import datetime, timedelta
from typing import cast

import pytest

from sat_descarga_masiva.application.ports.gateways import SatGateway
from sat_descarga_masiva.domain.enums.catalog import (
    Direction,
    DocumentStatus,
    RequestType,
    ServiceType,
)
from sat_descarga_masiva.domain.enums.request_state import RequestState
from sat_descarga_masiva.domain.enums.sat_status import SatStatusCode
from sat_descarga_masiva.domain.model.query import DownloadQuery
from sat_descarga_masiva.domain.model.results import Package, SubmitResult, VerificationResult
from sat_descarga_masiva.domain.model.token import AccessToken
from sat_descarga_masiva.domain.model.value_objects import (
    DateRange,
    PackageId,
    RequestId,
    Rfc,
)
from sat_descarga_masiva.infrastructure.sat.gateways.satcfdi_gateway import SatcfdiGateway

RFC = Rfc("AAA010101AAA")
RID = "4e80345d-917f-40bb-a98f-4a73939353c5"


class FakeClock:
    def now(self) -> datetime:
        return datetime(2026, 1, 1)


class FakeSat:
    """Double for satcfdi.pacs.sat.SAT (the subset SatcfdiGateway uses)."""

    def recover_comprobante_received_request(self, **kwargs: object) -> dict[str, object]:
        return {"IdSolicitud": RID, "CodEstatus": "5000", "Mensaje": "Solicitud Aceptada"}

    def recover_comprobante_status(self, id_solicitud: str) -> dict[str, object]:
        return {
            "EstadoSolicitud": 3,
            "CodEstatus": "5000",
            "NumeroCFDIs": 1,
            "Mensaje": "Solicitud Aceptada",
            "IdsPaquetes": [f"{RID}_01"],
        }

    def recover_comprobante_download(self, id_paquete: str) -> tuple[dict[str, object], str]:
        return ({"CodEstatus": "5000"}, base64.b64encode(b"PK\x03\x04zip").decode("ascii"))


def _query() -> DownloadQuery:
    return DownloadQuery(
        service=ServiceType.CFDI,
        direction=Direction.RECIBIDOS,
        request_type=RequestType.CFDI,
        date_range=DateRange(datetime(2026, 1, 1), datetime(2026, 1, 31)),
        rfc_solicitante=RFC,
        document_status=DocumentStatus.VIGENTE,
    )


class FakeSatGateway:
    """In-memory SAT gateway double implementing the four gateway ports."""

    def authenticate(self, identity: object) -> AccessToken:
        now = datetime(2026, 1, 1)
        return AccessToken("t", now, now + timedelta(minutes=30))

    def request(self, query: DownloadQuery, token: AccessToken) -> SubmitResult:
        return SubmitResult(RequestId(RID), SatStatusCode("5000"), "Solicitud Aceptada")

    def verify(self, request_id: RequestId, rfc: Rfc, token: AccessToken) -> VerificationResult:
        return VerificationResult(
            state=RequestState.COMPLETED,
            cod_estatus=SatStatusCode("5000"),
            numero_cfdis=1,
            mensaje="Solicitud Aceptada",
            ids_paquetes=(PackageId(f"{RID}_01"),),
        )

    def download(self, package_id: PackageId, rfc: Rfc, token: AccessToken) -> Package:
        return Package(package_id, b"PK\x03\x04zip")


class FakeIdentity:
    """Minimal SigningIdentity double for authenticate()."""

    rfc = "AAA010101AAA"
    cer_base64 = "x"

    def sign(self, data: bytes) -> bytes:
        return b"sig"


def _fake_conforms(g: FakeSatGateway) -> SatGateway:
    """mypy proof that FakeSatGateway satisfies the SatGateway Protocol (AGENT.md §12)."""
    return g


def _satcfdi_factory() -> SatcfdiGateway:
    """Build SatcfdiGateway over the FakeSat double (no live SAT in unit tests)."""
    return SatcfdiGateway(sat=FakeSat(), signer=FakeIdentity(), clock=FakeClock())


def _satcfdi_conforms(g: SatcfdiGateway) -> SatGateway:
    """mypy proof that SatcfdiGateway satisfies the SatGateway Protocol (§12)."""
    return g


GATEWAY_FACTORIES = [
    pytest.param(FakeSatGateway, id="FakeSatGateway"),
    pytest.param(_satcfdi_factory, id="SatcfdiGateway"),
]


@pytest.fixture(params=GATEWAY_FACTORIES)
def gateway(request: pytest.FixtureRequest) -> SatGateway:
    return cast(SatGateway, request.param())


def test_authenticate_returns_repr_safe_token(gateway: SatGateway) -> None:
    token = gateway.authenticate(FakeIdentity())
    assert token.is_valid(datetime(2026, 1, 1))
    assert repr(token) == "AccessToken(***)"


def test_request_returns_request_id_and_ok(gateway: SatGateway) -> None:
    token = gateway.authenticate(FakeIdentity())
    result = gateway.request(_query(), token)
    assert result.request_id is not None
    assert result.cod_estatus.value == "5000"


def test_verify_returns_completed_with_packages(gateway: SatGateway) -> None:
    token = gateway.authenticate(FakeIdentity())
    result = gateway.verify(RequestId(RID), RFC, token)
    assert result.state is RequestState.COMPLETED
    assert len(result.ids_paquetes) == 1


def test_download_returns_package_with_matching_id(gateway: SatGateway) -> None:
    token = gateway.authenticate(FakeIdentity())
    pid = PackageId(f"{RID}_01")
    package = gateway.download(pid, RFC, token)
    assert package.package_id == pid
    assert package.content

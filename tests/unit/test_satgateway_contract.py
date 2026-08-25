"""SatGateway adapter contract (AGENT.md §12): proves swappability.

The same behavior assertions run against every SAT-gateway implementation.
Currently only the FakeSatGateway double exists; SatcfdiGateway is added to
GATEWAY_FACTORIES when M1 lands it — the suite then proves interchangeability.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

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

RFC = Rfc("AAA010101AAA")
RID = "4e80345d-917f-40bb-a98f-4a73939353c5"


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


GATEWAY_FACTORIES = [pytest.param(FakeSatGateway, id="FakeSatGateway")]


@pytest.fixture(params=GATEWAY_FACTORIES)
def gateway(request: pytest.FixtureRequest) -> object:
    return request.param()


def test_authenticate_returns_repr_safe_token(gateway: object) -> None:
    token = gateway.authenticate(RFC)
    assert token.is_valid(datetime(2026, 1, 1))
    assert repr(token) == "AccessToken(***)"


def test_request_returns_request_id_and_ok(gateway: object) -> None:
    token = gateway.authenticate(RFC)
    result = gateway.request(_query(), token)
    assert result.request_id is not None
    assert result.cod_estatus.value == "5000"


def test_verify_returns_completed_with_packages(gateway: object) -> None:
    token = gateway.authenticate(RFC)
    result = gateway.verify(RequestId(RID), RFC, token)
    assert result.state is RequestState.COMPLETED
    assert len(result.ids_paquetes) == 1


def test_download_returns_package_with_matching_id(gateway: object) -> None:
    token = gateway.authenticate(RFC)
    pid = PackageId(f"{RID}_01")
    package = gateway.download(pid, RFC, token)
    assert package.package_id == pid
    assert package.content

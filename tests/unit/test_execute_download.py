"""Demonstrates the hexagonal payoff: the whole workflow, no network/sleep/FIEL/SAT."""

from datetime import datetime, timedelta

import pytest

from sat_descarga_masiva.application.policies.backoff import ImmediateBackoff, PollingPolicy
from sat_descarga_masiva.application.use_cases.execute_download import ExecuteDownloadUseCase
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
from sat_descarga_masiva.domain.model.value_objects import DateRange, PackageId, RequestId, Rfc

RID = "4e80345d-917f-40bb-a98f-4a73939343c5"


class FakeClock:
    def now(self) -> datetime:
        return datetime(2026, 1, 1)


class FakeAuth:
    def authenticate(self, identity: object) -> AccessToken:
        now = datetime(2026, 1, 1)
        return AccessToken("t", now, now + timedelta(minutes=5))


class FakeRequests:
    def request(self, query: object, token: object) -> SubmitResult:
        return SubmitResult(RequestId(RID), SatStatusCode("5000"), "ok")


class FakeVerifier:
    def verify(self, request_id: object, rfc: object, token: object) -> VerificationResult:
        return VerificationResult(
            RequestState.COMPLETED, SatStatusCode("5000"), ids_paquetes=(PackageId(f"{RID}_01"),)
        )


class FakeDownloader:
    def download(self, package_id: PackageId, rfc: object, token: object) -> Package:
        return Package(package_id, b"PK\x03\x04zip")


class FakeRepo:
    def save(self, request_id: object) -> None: ...
    def get(self, request_id: object) -> None:
        return None


class FakeIdentity:
    rfc = "AAA010101AAA"
    cer_base64 = "x"

    def sign(self, data: bytes) -> bytes:
        return b"sig"


@pytest.mark.xfail(reason="TDD: implement ExecuteDownloadUseCase.execute", strict=False)
def test_workflow_returns_packages() -> None:
    uc = ExecuteDownloadUseCase(
        identity=FakeIdentity(),
        auth=FakeAuth(),
        requests_gw=FakeRequests(),
        verifier=FakeVerifier(),
        downloader=FakeDownloader(),
        repository=FakeRepo(),
        clock=FakeClock(),
        backoff=ImmediateBackoff(),
        polling=PollingPolicy(timedelta(seconds=1), timedelta(seconds=1)),
    )
    query = DownloadQuery(
        service=ServiceType.CFDI,
        direction=Direction.RECIBIDOS,
        request_type=RequestType.CFDI,
        date_range=DateRange(datetime(2026, 1, 1), datetime(2026, 1, 31)),
        rfc_solicitante=Rfc("AAA010101AAA"),
        document_status=DocumentStatus.VIGENTE,
    )
    packages = uc.execute(query)
    assert len(packages) == 1

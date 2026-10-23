"""M1-H: the verify step acts on classify() (H1-H10). Offline fakes only."""

from __future__ import annotations

import types
from datetime import datetime, timedelta

import pytest

from sat_descarga_masiva.application.policies import sat_status as policy
from sat_descarga_masiva.application.policies.backoff import ImmediateBackoff, PollingPolicy
from sat_descarga_masiva.application.policies.sat_status import classify
from sat_descarga_masiva.application.use_cases import execute_download as flow
from sat_descarga_masiva.application.use_cases.execute_download import ExecuteDownloadUseCase
from sat_descarga_masiva.domain.enums.catalog import (
    Direction,
    DocumentStatus,
    RequestType,
    ServiceType,
)
from sat_descarga_masiva.domain.enums.request_state import RequestState
from sat_descarga_masiva.domain.enums.sat_status import ErrorClassification, SatStatusCode
from sat_descarga_masiva.domain.errors import (
    AuthenticationError,
    SatRequestFailedError,
)
from sat_descarga_masiva.domain.model.query import DownloadQuery
from sat_descarga_masiva.domain.model.results import Package, SubmitResult, VerificationResult
from sat_descarga_masiva.domain.model.token import AccessToken
from sat_descarga_masiva.domain.model.value_objects import DateRange, PackageId, RequestId, Rfc
from sat_descarga_masiva.infrastructure.sat.gateways.satcfdi_gateway import SatcfdiGateway

RID = "4e80345d-917f-40bb-a98f-4a73939353c5"
TOKEN_SECRET = "SUPERSECRET-TOKEN"


class FakeClock:
    def now(self) -> datetime:
        return datetime(2026, 1, 1)


class FakeSleeper:
    def __init__(self) -> None:
        self.sleeps: list[timedelta] = []

    def sleep(self, duration: timedelta) -> None:
        self.sleeps.append(duration)


class FakeAuth:
    def authenticate(self, identity: object) -> AccessToken:
        now = datetime(2026, 1, 1)
        return AccessToken(TOKEN_SECRET, now, now + timedelta(minutes=5))


class FakeRequests:
    def request(self, query: object, token: object) -> SubmitResult:
        return SubmitResult(RequestId(RID), SatStatusCode("5000"), "ok")


class ScriptedVerifier:
    """Returns a scripted sequence, repeating the last response once exhausted."""

    def __init__(self, *results: VerificationResult) -> None:
        self._results = list(results)
        self.calls = 0

    def verify(self, request_id: object, rfc: object, token: object) -> VerificationResult:
        result = self._results[min(self.calls, len(self._results) - 1)]
        self.calls += 1
        return result


class FakeDownloader:
    def __init__(self) -> None:
        self.requested: list[PackageId] = []

    def download(self, package_id: PackageId, rfc: object, token: object) -> Package:
        self.requested.append(package_id)
        return Package(package_id, b"PK")


class FakeRepo:
    def save(self, request_id: object) -> None: ...
    def get(self, request_id: object) -> None:
        return None


class FakeIdentity:
    rfc = "AAA010101AAA"
    cer_base64 = "x"

    def sign(self, data: bytes) -> bytes:
        return b"sig"


def _result(
    state: RequestState, code: str = "5000", packages: tuple[PackageId, ...] = ()
) -> VerificationResult:
    return VerificationResult(state, SatStatusCode(code), ids_paquetes=packages)


def _query() -> DownloadQuery:
    return DownloadQuery(
        service=ServiceType.CFDI,
        direction=Direction.RECIBIDOS,
        request_type=RequestType.CFDI,
        date_range=DateRange(datetime(2026, 1, 1), datetime(2026, 1, 31)),
        rfc_solicitante=Rfc("AAA010101AAA"),
        document_status=DocumentStatus.VIGENTE,
    )


def build_uc(
    verifier: object,
    clock: object | None = None,
    sleeper: FakeSleeper | None = None,
    downloader: object | None = None,
) -> ExecuteDownloadUseCase:
    return ExecuteDownloadUseCase(
        identity=FakeIdentity(),
        auth=FakeAuth(),
        requests_gw=FakeRequests(),
        verifier=verifier,
        downloader=downloader if downloader is not None else FakeDownloader(),
        repository=FakeRepo(),
        clock=clock if clock is not None else FakeClock(),
        backoff=ImmediateBackoff(),
        polling=PollingPolicy(timedelta(seconds=1), timedelta(seconds=1)),
        sleeper=sleeper if sleeper is not None else FakeSleeper(),
    )


# --- H1 / H2: codes that look terminal but were mis-mapped ---


def test_h1_lifetime_quota_fails_fast_without_waiting() -> None:
    """5002 used to classify as FATAL, so the flow spun until the deadline."""
    sleeper = FakeSleeper()
    uc = build_uc(ScriptedVerifier(_result(RequestState.ACCEPTED, "5002")), sleeper=sleeper)
    with pytest.raises(SatRequestFailedError):
        uc.execute(_query())
    assert sleeper.sleeps == []


def test_h2_daily_quota_returns_an_empty_outcome() -> None:
    """5011 is QUOTA_WAIT, so nothing is downloaded and no deadline is burned."""
    sleeper = FakeSleeper()
    downloader = FakeDownloader()
    uc = build_uc(
        ScriptedVerifier(_result(RequestState.ACCEPTED, "5011")),
        sleeper=sleeper,
        downloader=downloader,
    )
    outcome = uc.execute(_query())
    assert outcome.request_id == RequestId(RID)
    assert outcome.packages == ()
    assert downloader.requested == []
    assert sleeper.sleeps == []


def test_h10_the_flow_and_the_contract_share_the_classification() -> None:
    """Flow and contract must not drift: they read the same policy module."""
    assert flow.classify_verification is policy.classify_verification
    assert flow.ErrorClassification is policy.ErrorClassification


# --- H3 / H4: terminal failures are raised, not swallowed ---


@pytest.mark.parametrize("state", [RequestState.ERROR, RequestState.REJECTED, RequestState.EXPIRED])
def test_h3_terminal_failure_states_raise_state(state: RequestState) -> None:
    uc = build_uc(ScriptedVerifier(_result(state)))
    with pytest.raises(SatRequestFailedError) as excinfo:
        uc.execute(_query())
    assert excinfo.value.estado_solicitud == int(state)
    assert excinfo.value.cod_estatus == "5000"


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        ("5002", ErrorClassification.FATAL),
        ("5003", ErrorClassification.PARTITION),
        ("5011", ErrorClassification.QUOTA_WAIT),
    ],
)
def test_h4_enriched_codes_keep_their_classification(
    code: str, expected: ErrorClassification
) -> None:
    assert classify(code) is expected


# --- H5 / H6: happy path still downloads, 404 is not fatal ---


def test_h5_completed_with_packages_downloads_each_one() -> None:
    first, second = PackageId("PKG-1"), PackageId("PKG-2")
    downloader = FakeDownloader()
    uc = build_uc(
        ScriptedVerifier(_result(RequestState.COMPLETED, packages=(first, second))),
        downloader=downloader,
    )
    outcome = uc.execute(_query())
    assert outcome.state is RequestState.COMPLETED
    assert [package.package_id for package in outcome.packages] == [first, second]
    assert downloader.requested == [first, second]


def test_h6_not_found_is_an_empty_outcome_not_a_failure() -> None:
    downloader = FakeDownloader()
    uc = build_uc(ScriptedVerifier(_result(RequestState.COMPLETED, "5004")), downloader=downloader)
    outcome = uc.execute(_query())
    assert outcome.packages == ()
    assert downloader.requested == []


# --- H8 / H9: authentication failures and secret hygiene ---


def test_h8_authentication_failure_raises_authentication_error() -> None:
    uc = build_uc(ScriptedVerifier(_result(RequestState.ERROR, "300")))
    with pytest.raises(AuthenticationError):
        uc.execute(_query())


def test_h9_raised_errors_never_leak_the_access_token() -> None:
    uc = build_uc(ScriptedVerifier(_result(RequestState.REJECTED, "301")))
    with pytest.raises(SatRequestFailedError) as excinfo:
        uc.execute(_query())
    assert TOKEN_SECRET not in str(excinfo.value)


# --- H10b: the gateway hands over the event code, not the transport status ---


def test_gateway_prefers_the_event_code_over_codestatus() -> None:
    gateway = object.__new__(SatcfdiGateway)
    gateway._sat = types.SimpleNamespace(
        recover_comprobante_status=lambda request_id: {
            "EstadoSolicitud": "3",
            "CodEstatus": "5000",
            "CodigoEstadoSolicitud": "5004",
        }
    )
    token = FakeAuth().authenticate(None)
    result = gateway.verify(RequestId(RID), Rfc("AAA010101AAA"), token)
    assert result.cod_estatus.value == "5004"

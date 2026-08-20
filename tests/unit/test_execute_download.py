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
from sat_descarga_masiva.domain.errors import SatTimeoutError
from sat_descarga_masiva.domain.model.query import DownloadQuery
from sat_descarga_masiva.domain.model.results import Package, SubmitResult, VerificationResult
from sat_descarga_masiva.domain.model.token import AccessToken
from sat_descarga_masiva.domain.model.value_objects import DateRange, PackageId, RequestId, Rfc

RID = "4e80345d-917f-40bb-a98f-4a73939353c5"


class FakeClock:
    def __init__(self, now: datetime = datetime(2026, 1, 1)) -> None:
        self._now = now

    def now(self) -> datetime:
        return self._now


class AdvancingClock:
    """Returns an ever-later now so a poll loop can outlive its deadline."""

    def __init__(self) -> None:
        self._now = datetime(2026, 1, 1)

    def now(self) -> datetime:
        current = self._now
        self._now += timedelta(hours=1)
        return current


class FakeSleeper:
    def __init__(self) -> None:
        self.sleeps: list[timedelta] = []

    def sleep(self, duration: timedelta) -> None:
        self.sleeps.append(duration)


class FakeAuth:
    def authenticate(self, identity: object) -> AccessToken:
        now = datetime(2026, 1, 1)
        return AccessToken("t", now, now + timedelta(minutes=5))


class FakeRequests:
    def request(self, query: object, token: object) -> SubmitResult:
        return SubmitResult(RequestId(RID), SatStatusCode("5000"), "ok")


class FakeVerifier:
    def __init__(
        self,
        state: RequestState = RequestState.COMPLETED,
        packages: tuple[PackageId, ...] = (PackageId(f"{RID}_01"),),
    ) -> None:
        self._state = state
        self._packages = packages

    def verify(self, request_id: object, rfc: object, token: object) -> VerificationResult:
        return VerificationResult(self._state, SatStatusCode("5000"), ids_paquetes=self._packages)


class SequentialVerifier:
    """Returns a scripted sequence of states; proves the poll loop advances."""

    def __init__(self, states: list[RequestState]) -> None:
        self._states = list(states)

    def verify(self, request_id: object, rfc: object, token: object) -> VerificationResult:
        return VerificationResult(self._states.pop(0), SatStatusCode("5000"))


class FakeDownloader:
    def download(self, package_id: PackageId, rfc: object, token: object) -> Package:
        return Package(package_id, b"PK\x03\x04zip")


class ExplodingDownloader:
    def download(self, package_id: PackageId, rfc: object, token: object) -> Package:
        raise AssertionError("download must never be attempted when state is not COMPLETED")


class FakeRepo:
    def save(self, request_id: object) -> None: ...
    def get(self, request_id: object) -> None:
        return None


class FakeIdentity:
    rfc = "AAA010101AAA"
    cer_base64 = "x"

    def sign(self, data: bytes) -> bytes:
        return b"sig"


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


def _query() -> DownloadQuery:
    return DownloadQuery(
        service=ServiceType.CFDI,
        direction=Direction.RECIBIDOS,
        request_type=RequestType.CFDI,
        date_range=DateRange(datetime(2026, 1, 1), datetime(2026, 1, 31)),
        rfc_solicitante=Rfc("AAA010101AAA"),
        document_status=DocumentStatus.VIGENTE,
    )


def test_workflow_returns_packages() -> None:
    packages = build_uc(FakeVerifier()).execute(_query())
    assert len(packages) == 1
    assert packages[0].package_id == PackageId(f"{RID}_01")


def test_terminal_error_returns_no_packages() -> None:
    uc = build_uc(FakeVerifier(RequestState.ERROR), downloader=ExplodingDownloader())
    assert uc.execute(_query()) == []


def test_raises_timeout_after_deadline() -> None:
    uc = build_uc(FakeVerifier(RequestState.PROCESSING), clock=AdvancingClock())
    with pytest.raises(SatTimeoutError):
        uc.execute(_query())


def test_sleeps_with_backoff_between_polls() -> None:
    sleeper = FakeSleeper()
    states = [RequestState.PROCESSING, RequestState.ACCEPTED, RequestState.COMPLETED]
    uc = build_uc(SequentialVerifier(states), sleeper=sleeper)
    uc.execute(_query())
    assert len(sleeper.sleeps) == 2
    assert all(d == timedelta(0) for d in sleeper.sleeps)

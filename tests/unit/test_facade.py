"""M1: SatDownloadClient facade — download + ingest orchestration (§4 #9)."""

from datetime import UTC, datetime

from sat_descarga_masiva.domain.enums.catalog import (
    Direction,
    DocumentStatus,
    RequestType,
    ServiceType,
)
from sat_descarga_masiva.domain.enums.request_state import RequestState
from sat_descarga_masiva.domain.model.manifest import Manifest
from sat_descarga_masiva.domain.model.query import DownloadQuery
from sat_descarga_masiva.domain.model.results import DownloadOutcome, Package
from sat_descarga_masiva.domain.model.source import ExtractedXml, sha256_hex
from sat_descarga_masiva.domain.model.value_objects import DateRange, PackageId, RequestId, Rfc
from sat_descarga_masiva.facade.client import SatDownloadClient, VersionInfo

RFC = Rfc("AAA010101AAA")
RID = RequestId("4e80345d-917f-40bb-a98f-4a73939353c5")
VERSIONS = VersionInfo(satcfdi_version="26.8.0", application_version="0.1.0")
NOW = datetime(2026, 1, 31, 12, tzinfo=UTC)
POLICY_VERSION = 7


def _query() -> DownloadQuery:
    return DownloadQuery(
        service=ServiceType.CFDI,
        direction=Direction.RECIBIDOS,
        request_type=RequestType.CFDI,
        date_range=DateRange(datetime(2026, 1, 1), datetime(2026, 1, 31)),
        rfc_solicitante=RFC,
        document_status=DocumentStatus.VIGENTE,
    )


class FakeClock:
    def now(self) -> datetime:
        return NOW


class FakeExecutor:
    def __init__(self, outcome: DownloadOutcome) -> None:
        self._outcome = outcome
        self.queries: list[DownloadQuery] = []

    def execute(self, query: DownloadQuery) -> DownloadOutcome:
        self.queries.append(query)
        return self._outcome


class FakeIngester:
    def __init__(self, result: tuple[ExtractedXml, ...] = ()) -> None:
        self._result = result
        self.calls: list[tuple[Package, Manifest]] = []

    def ingest(self, package: Package, manifest: Manifest) -> tuple[ExtractedXml, ...]:
        self.calls.append((package, manifest))
        return self._result


def _client(executor: FakeExecutor, ingester: FakeIngester) -> SatDownloadClient:
    return SatDownloadClient(
        executor=executor,
        ingester=ingester,
        clock=FakeClock(),
        versions=VERSIONS,
        policy_version=POLICY_VERSION,
    )


def test_one_package_chains_download_to_ingest() -> None:
    content = b"PKzip"
    package = Package(PackageId(f"{RID}_01"), content)
    extracted = (ExtractedXml("u1", "ingreso", sha256_hex(b"<x/>")),)
    executor = FakeExecutor(
        DownloadOutcome(request_id=RID, state=RequestState.COMPLETED, packages=(package,))
    )
    ingester = FakeIngester(result=extracted)

    result = _client(executor, ingester).download_and_ingest(_query())

    assert executor.queries == [_query()]
    assert len(ingester.calls) == 1
    called_package, manifest = ingester.calls[0]
    assert called_package is package
    assert manifest.request_id == RID
    assert manifest.query == _query()
    assert manifest.package_id == package.package_id
    assert manifest.sha256 == sha256_hex(content)  # ZIP bytes, not extracted XML
    assert manifest.downloaded_at == NOW
    assert manifest.satcfdi_version == "26.8.0"
    assert manifest.application_version == "0.1.0"
    assert manifest.policy_version == POLICY_VERSION
    assert result == extracted


def test_multiple_packages_each_get_own_manifest_in_order() -> None:
    p1 = Package(PackageId(f"{RID}_01"), b"zip1")
    p2 = Package(PackageId(f"{RID}_02"), b"zip2")
    executor = FakeExecutor(
        DownloadOutcome(request_id=RID, state=RequestState.COMPLETED, packages=(p1, p2))
    )
    ingester = FakeIngester(result=(ExtractedXml("u1", "ingreso", "h"),))

    _client(executor, ingester).download_and_ingest(_query())

    assert len(ingester.calls) == 2
    for i, (called_package, manifest) in enumerate(ingester.calls):
        expected = p1 if i == 0 else p2
        assert called_package is expected
        assert manifest.request_id == RID  # same request_id on every manifest
        assert manifest.package_id == expected.package_id
        assert manifest.sha256 == sha256_hex(expected.content)


def test_zero_packages_is_noop() -> None:
    executor = FakeExecutor(
        DownloadOutcome(request_id=RID, state=RequestState.COMPLETED, packages=())
    )
    ingester = FakeIngester()

    result = _client(executor, ingester).download_and_ingest(_query())

    assert result == ()
    assert ingester.calls == []

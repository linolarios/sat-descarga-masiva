"""M1: IngestDownloadedPackageUseCase — store raw -> extract (§6a #4)."""

from datetime import datetime

import pytest

from sat_descarga_masiva.application.use_cases.ingest_downloaded_package import (
    IngestDownloadedPackageUseCase,
)
from sat_descarga_masiva.domain.enums.catalog import (
    Direction,
    DocumentStatus,
    RequestType,
    ServiceType,
)
from sat_descarga_masiva.domain.errors import ExtractionError
from sat_descarga_masiva.domain.model.manifest import Manifest, build_manifest
from sat_descarga_masiva.domain.model.query import DownloadQuery
from sat_descarga_masiva.domain.model.results import Package
from sat_descarga_masiva.domain.model.source import ExtractedXml, sha256_hex
from sat_descarga_masiva.domain.model.value_objects import (
    DateRange,
    PackageId,
    RequestId,
    Rfc,
)
from sat_descarga_masiva.infrastructure.source.filesystem_sink import (
    FilesystemSourceArtifactSink,
)

RFC = Rfc("AAA010101AAA")
RID = RequestId("4e80345d-917f-40bb-a98f-4a73939353c5")
PID = PackageId("4e80345d-917f-40bb-a98f-4a73939353c5_01")

QUERY = DownloadQuery(
    service=ServiceType.CFDI,
    direction=Direction.RECIBIDOS,
    request_type=RequestType.CFDI,
    date_range=DateRange(datetime(2026, 1, 1), datetime(2026, 1, 31)),
    rfc_solicitante=RFC,
    document_status=DocumentStatus.VIGENTE,
)


def _manifest(content: bytes) -> Manifest:
    return build_manifest(
        sha256=sha256_hex(content),
        client_rfc=RFC,
        service=ServiceType.CFDI,
        direction=Direction.RECIBIDOS,
        request_id=RID,
        package_id=PID,
        downloaded_at=datetime(2026, 1, 31, 12),
        satcfdi_version="26.8.0",
        application_version="0.1.0",
        query=QUERY,
        policy_version=1,
    )


class RecordingSink:
    def __init__(self) -> None:
        self.stored: list[tuple[Manifest, bytes]] = []

    def store(self, manifest: Manifest, content: bytes) -> None:
        self.stored.append((manifest, content))


class FakeExtractor:
    def __init__(
        self,
        result: tuple[ExtractedXml, ...] = (),
        error: Exception | None = None,
    ) -> None:
        self._result = result
        self._error = error
        self.calls: list[bytes] = []

    def extract(self, package_id: PackageId, content: bytes) -> tuple[ExtractedXml, ...]:
        self.calls.append(content)
        if self._error is not None:
            raise self._error
        return self._result


def test_ingest_stores_raw_then_extracts() -> None:
    content = b"PK\x03\x04zip"
    sink = RecordingSink()
    expected = (ExtractedXml("u1", "ingreso", sha256_hex(b"<x/>")),)
    extractor = FakeExtractor(result=expected)
    uc = IngestDownloadedPackageUseCase(sink=sink, extractor=extractor)

    result = uc.ingest(Package(PID, content), _manifest(content))

    assert result == expected
    assert sink.stored == [(_manifest(content), content)]
    assert extractor.calls == [content]


def test_extraction_failure_leaves_raw_store_intact(tmp_path) -> None:
    content = b"PK\x03\x04zip"
    sink = FilesystemSourceArtifactSink(tmp_path)
    extractor = FakeExtractor(error=ExtractionError("boom"))
    uc = IngestDownloadedPackageUseCase(sink=sink, extractor=extractor)

    with pytest.raises(ExtractionError):
        uc.ingest(Package(PID, content), _manifest(content))

    # Raw zip was stored before extraction failed -> re-runnable, not corrupted.
    zip_path = tmp_path / "raw" / f"{sha256_hex(content)}.zip"
    assert zip_path.read_bytes() == content

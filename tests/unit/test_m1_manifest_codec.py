"""M1: manifest sidecar codec round-trip + store-integrity detection (§6, §6a.2).

`manifest.json` is the only record of what a stored package was, so it must round-trip
losslessly: a manifest written and read back has to be the same value, or a resumed run
cannot trust its own evidence. SHA-256 here is **store** integrity (§6a.2) — it proves our
stored copy has not changed since we saved it — never SAT authenticity (§6a.3).
"""

from datetime import datetime
from pathlib import Path

import pytest

from sat_descarga_masiva.domain.enums.catalog import (
    Direction,
    DocumentStatus,
    RequestType,
    ServiceType,
)
from sat_descarga_masiva.domain.errors import SourceIntegrityError
from sat_descarga_masiva.domain.model.manifest import Manifest, build_manifest
from sat_descarga_masiva.domain.model.query import DownloadQuery
from sat_descarga_masiva.domain.model.source import sha256_hex, verify_source_integrity
from sat_descarga_masiva.domain.model.value_objects import (
    DateRange,
    PackageId,
    RequestId,
    Rfc,
)
from sat_descarga_masiva.infrastructure.source.codec import (
    manifest_from_dict,
    manifest_to_dict,
)
from sat_descarga_masiva.infrastructure.source.filesystem_sink import (
    FilesystemSourceArtifactSink,
)

RFC = Rfc("AAA010101AAA")
OTHER_RFC = Rfc("BBB010101BBB")
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


def _manifest(sha256: str, *, query: DownloadQuery = QUERY) -> Manifest:
    return build_manifest(
        sha256=sha256,
        client_rfc=RFC,
        service=ServiceType.CFDI,
        direction=Direction.RECIBIDOS,
        request_id=RID,
        package_id=PID,
        downloaded_at=datetime(2026, 1, 31, 12),
        satcfdi_version="26.8.0",
        application_version="0.1.0",
        query=query,
        policy_version=1,
    )


def test_manifest_round_trips_through_its_sidecar_dict() -> None:
    manifest = _manifest(sha256_hex(b"PK\x03\x04"))
    assert manifest_from_dict(manifest_to_dict(manifest)) == manifest


def test_round_trip_carries_every_optional_query_field() -> None:
    query = DownloadQuery(
        service=ServiceType.CFDI,
        direction=Direction.EMITIDOS,
        request_type=RequestType.CFDI,
        date_range=DateRange(datetime(2026, 2, 1), datetime(2026, 2, 28)),
        rfc_solicitante=RFC,
        document_status=DocumentStatus.TODOS,
        rfc_emisor=RFC,
        rfc_receptor=OTHER_RFC,
    )
    manifest = _manifest(sha256_hex(b"x"), query=query)
    restored = manifest_from_dict(manifest_to_dict(manifest))
    assert restored.query == query
    assert restored.query.rfc_emisor == RFC
    assert restored.query.rfc_receptor == OTHER_RFC
    assert restored == manifest


def test_round_trip_preserves_the_download_instant() -> None:
    manifest = _manifest(sha256_hex(b"y"))
    restored = manifest_from_dict(manifest_to_dict(manifest))
    assert restored.downloaded_at == manifest.downloaded_at
    assert restored.query.date_range == manifest.query.date_range


def test_stored_zip_still_hashes_to_the_manifest_digest(tmp_path: Path) -> None:
    content = b"PK\x03\x04zip-evidence"
    manifest = _manifest(sha256_hex(content))
    FilesystemSourceArtifactSink(tmp_path).store(manifest, content)

    stored = (tmp_path / "raw" / f"{manifest.sha256}.zip").read_bytes()
    verify_source_integrity(stored, manifest.sha256)  # re-read, re-hash: no raise
    assert sha256_hex(stored) == manifest.sha256


def test_tampered_stored_zip_is_detected(tmp_path: Path) -> None:
    content = b"PK\x03\x04zip-evidence"
    manifest = _manifest(sha256_hex(content))
    sink = FilesystemSourceArtifactSink(tmp_path)
    sink.store(manifest, content)

    stored_path = tmp_path / "raw" / f"{manifest.sha256}.zip"
    stored_path.write_bytes(content + b"tampered")  # a changed byte a re-hash must catch
    with pytest.raises(SourceIntegrityError):
        verify_source_integrity(stored_path.read_bytes(), manifest.sha256)


def test_intact_content_passes_the_integrity_check() -> None:
    content = b"<cfdi:Comprobante/>"
    verify_source_integrity(content, sha256_hex(content))


def test_integrity_error_reports_both_digests() -> None:
    with pytest.raises(SourceIntegrityError) as excinfo:
        verify_source_integrity(b"actual", sha256_hex(b"expected"))
    assert sha256_hex(b"actual") in str(excinfo.value)
    assert sha256_hex(b"expected") in str(excinfo.value)

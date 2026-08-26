"""M1: FilesystemSourceArtifactSink — immutable raw source store (§6)."""

import json
from datetime import datetime

import pytest

from sat_descarga_masiva.domain.enums.catalog import (
    Direction,
    DocumentStatus,
    RequestType,
    ServiceType,
)
from sat_descarga_masiva.domain.model.manifest import Manifest, build_manifest
from sat_descarga_masiva.domain.model.query import DownloadQuery
from sat_descarga_masiva.domain.model.source import sha256_hex
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


def _manifest(sha: str) -> Manifest:
    return build_manifest(
        sha256=sha,
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


def test_sink_writes_zip_and_manifest_sidecar(tmp_path) -> None:
    content = b"PK\x03\x04zip"
    sha = sha256_hex(content)
    FilesystemSourceArtifactSink(tmp_path).store(_manifest(sha), content)

    zip_path = tmp_path / "raw" / f"{sha}.zip"
    manifest_path = tmp_path / "raw" / f"{sha}.manifest.json"
    assert zip_path.read_bytes() == content
    data = json.loads(manifest_path.read_text())
    assert data["sha256"] == sha
    assert data["client_rfc"] == "AAA010101AAA"
    assert data["query"]["date_start"] == "2026-01-01T00:00:00"


def test_sink_restore_is_idempotent_and_never_overwrites(tmp_path) -> None:
    content = b"PK\x03\x04zip"
    sha = sha256_hex(content)
    sink = FilesystemSourceArtifactSink(tmp_path)
    sink.store(_manifest(sha), content)
    sink.store(_manifest(sha), content)  # second store must be a no-op
    # one zip + one manifest sidecar — no overwrite, no duplication
    assert len(list((tmp_path / "raw").iterdir())) == 2


def test_sink_rejects_hash_mismatch(tmp_path) -> None:
    sink = FilesystemSourceArtifactSink(tmp_path)
    with pytest.raises(ValueError):
        sink.store(_manifest("deadbeef"), b"PK\x03\x04zip")
    assert not (tmp_path / "raw").exists()

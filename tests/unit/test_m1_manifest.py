"""M1: per-package Manifest — every §6 field present and frozen."""

from dataclasses import FrozenInstanceError
from datetime import datetime

import pytest

from sat_descarga_masiva.domain.enums.catalog import (
    Direction,
    DocumentStatus,
    RequestType,
    ServiceType,
)
from sat_descarga_masiva.domain.model.manifest import build_manifest
from sat_descarga_masiva.domain.model.query import DownloadQuery
from sat_descarga_masiva.domain.model.source import sha256_hex
from sat_descarga_masiva.domain.model.value_objects import (
    DateRange,
    PackageId,
    RequestId,
    Rfc,
)

RFC = Rfc("AAA010101AAA")
QUERY = DownloadQuery(
    service=ServiceType.CFDI,
    direction=Direction.RECIBIDOS,
    request_type=RequestType.CFDI,
    date_range=DateRange(datetime(2026, 1, 1), datetime(2026, 1, 31)),
    rfc_solicitante=RFC,
    document_status=DocumentStatus.VIGENTE,
)


def test_manifest_holds_every_s6_field() -> None:
    rid = RequestId("4e80345d-917f-40bb-a98f-4a73939353c5")
    pid = PackageId("4e80345d-917f-40bb-a98f-4a73939353c5_01")
    manifest = build_manifest(
        sha256=sha256_hex(b"PK\x03\x04"),
        client_rfc=RFC,
        service=ServiceType.CFDI,
        direction=Direction.RECIBIDOS,
        request_id=rid,
        package_id=pid,
        downloaded_at=datetime(2026, 1, 31, 12),
        satcfdi_version="0.1.0",
        application_version="0.1.0",
        query=QUERY,
        policy_version=1,
    )
    assert manifest.sha256 == sha256_hex(b"PK\x03\x04")
    assert manifest.client_rfc == RFC
    assert manifest.service is ServiceType.CFDI
    assert manifest.request_id == rid
    assert manifest.package_id == pid
    assert manifest.query == QUERY


def test_manifest_is_immutable() -> None:
    rid = RequestId("4e80345d-917f-40bb-a98f-4a73939354d0")
    pid = PackageId("4e80345d-917f-40bb-a98f-4a73939354d0_01")
    manifest = build_manifest(
        sha256=sha256_hex(b"x"),
        client_rfc=RFC,
        service=ServiceType.CFDI,
        direction=Direction.RECIBIDOS,
        request_id=rid,
        package_id=pid,
        downloaded_at=datetime(2026, 1, 31, 12),
        satcfdi_version="v",
        application_version="v",
        query=QUERY,
        policy_version=1,
    )
    with pytest.raises(FrozenInstanceError):
        manifest.policy_version = 2

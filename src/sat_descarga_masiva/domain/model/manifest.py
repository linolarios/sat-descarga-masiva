"""Per-package acquisition manifest (§6). Domain-only, deterministic."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sat_descarga_masiva.domain.enums.catalog import Direction, ServiceType
from sat_descarga_masiva.domain.model.query import DownloadQuery
from sat_descarga_masiva.domain.model.value_objects import PackageId, RequestId, Rfc


@dataclass(frozen=True)
class Manifest:
    """Immutable evidence of one downloaded package (manifest.json shape, §6)."""

    sha256: str
    client_rfc: Rfc
    service: ServiceType
    direction: Direction
    request_id: RequestId
    package_id: PackageId
    downloaded_at: datetime
    satcfdi_version: str
    application_version: str
    query: DownloadQuery
    policy_version: int


def build_manifest(  # noqa: PLR0913  (each field is a defined §6 manifest key)
    *,
    sha256: str,
    client_rfc: Rfc,
    service: ServiceType,
    direction: Direction,
    request_id: RequestId,
    package_id: PackageId,
    downloaded_at: datetime,
    satcfdi_version: str,
    application_version: str,
    query: DownloadQuery,
    policy_version: int,
) -> Manifest:
    """Compose a manifest from explicit inputs (no network/satcfdi here)."""
    return Manifest(
        sha256=sha256,
        client_rfc=client_rfc,
        service=service,
        direction=direction,
        request_id=request_id,
        package_id=package_id,
        downloaded_at=downloaded_at,
        satcfdi_version=satcfdi_version,
        application_version=application_version,
        query=query,
        policy_version=policy_version,
    )

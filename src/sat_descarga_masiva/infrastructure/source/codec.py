"""JSON marshalling of the domain Manifest for the manifest.json sidecar (§6)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sat_descarga_masiva.domain.enums.catalog import (
    Direction,
    DocumentStatus,
    RequestType,
    ServiceType,
)
from sat_descarga_masiva.domain.model.manifest import Manifest
from sat_descarga_masiva.domain.model.query import DownloadQuery
from sat_descarga_masiva.domain.model.value_objects import (
    DateRange,
    PackageId,
    RequestId,
    Rfc,
)


def manifest_to_dict(manifest: Manifest) -> dict[str, Any]:
    """Lossless, deterministic JSON-friendly dict of a Manifest."""
    return {
        "sha256": manifest.sha256,
        "client_rfc": manifest.client_rfc.value,
        "service": manifest.service.value,
        "direction": manifest.direction.value,
        "request_id": manifest.request_id.value,
        "package_id": manifest.package_id.value,
        "downloaded_at": manifest.downloaded_at.isoformat(),
        "satcfdi_version": manifest.satcfdi_version,
        "application_version": manifest.application_version,
        "query": _query_to_dict(manifest.query),
        "policy_version": manifest.policy_version,
    }


def manifest_from_dict(payload: dict[str, Any]) -> Manifest:
    """Inverse of manifest_to_dict: the sidecar a resumed run reads back (§6).

    Rebuilds domain values through their constructors, so a corrupted/edited
    sidecar fails loudly instead of yielding an unvalidated manifest.
    """
    query = _query_from_dict(payload["query"])
    return Manifest(
        sha256=payload["sha256"],
        client_rfc=Rfc(payload["client_rfc"]),
        service=ServiceType(payload["service"]),
        direction=Direction(payload["direction"]),
        request_id=RequestId(payload["request_id"]),
        package_id=PackageId(payload["package_id"]),
        downloaded_at=datetime.fromisoformat(payload["downloaded_at"]),
        satcfdi_version=payload["satcfdi_version"],
        application_version=payload["application_version"],
        query=query,
        policy_version=payload["policy_version"],
    )


def _query_to_dict(query: DownloadQuery) -> dict[str, Any]:
    return {
        "service": query.service.value,
        "direction": query.direction.value,
        "request_type": query.request_type.value,
        "date_start": query.date_range.start.isoformat(),
        "date_end": query.date_range.end.isoformat(),
        "rfc_solicitante": query.rfc_solicitante.value,
        "document_status": query.document_status.value,
        "rfc_emisor": query.rfc_emisor.value if query.rfc_emisor is not None else None,
        "rfc_receptor": query.rfc_receptor.value if query.rfc_receptor is not None else None,
    }


def _query_from_dict(payload: dict[str, Any]) -> DownloadQuery:
    emisor = payload["rfc_emisor"]
    receptor = payload["rfc_receptor"]
    return DownloadQuery(
        service=ServiceType(payload["service"]),
        direction=Direction(payload["direction"]),
        request_type=RequestType(payload["request_type"]),
        date_range=DateRange(
            datetime.fromisoformat(payload["date_start"]),
            datetime.fromisoformat(payload["date_end"]),
        ),
        rfc_solicitante=Rfc(payload["rfc_solicitante"]),
        document_status=DocumentStatus(payload["document_status"]),
        rfc_emisor=Rfc(emisor) if emisor is not None else None,
        rfc_receptor=Rfc(receptor) if receptor is not None else None,
    )

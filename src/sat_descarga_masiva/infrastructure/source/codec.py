"""JSON marshalling of the domain Manifest for the manifest.json sidecar (§6)."""

from __future__ import annotations

from typing import Any

from sat_descarga_masiva.domain.model.manifest import Manifest
from sat_descarga_masiva.domain.model.query import DownloadQuery


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

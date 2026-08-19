"""The request the caller wants to run."""

from __future__ import annotations

from dataclasses import dataclass

from sat_descarga_masiva.domain.enums.catalog import (
    Direction,
    DocumentStatus,
    RequestType,
    ServiceType,
)
from sat_descarga_masiva.domain.model.value_objects import DateRange, Rfc


@dataclass(frozen=True)
class DownloadQuery:
    service: ServiceType
    direction: Direction
    request_type: RequestType
    date_range: DateRange
    rfc_solicitante: Rfc
    document_status: DocumentStatus = DocumentStatus.TODOS
    rfc_emisor: Rfc | None = None
    rfc_receptor: Rfc | None = None

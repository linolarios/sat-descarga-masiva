"""DownloadCursor — a resume marker. Pure data, no I/O (domain value object)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from sat_descarga_masiva.domain.enums.catalog import Direction, ServiceType
from sat_descarga_masiva.domain.model.value_objects import RequestId, Rfc


@dataclass(frozen=True)
class DownloadCursor:
    """Where an interruption resume must NOT leave a gap (§6 / §11 M1)."""

    client_rfc: Rfc
    service: ServiceType
    direction: Direction
    query_start: datetime
    query_end: datetime
    last_successful_boundary: datetime | None = None
    last_request_id: RequestId | None = None
    last_completed_at: datetime | None = None

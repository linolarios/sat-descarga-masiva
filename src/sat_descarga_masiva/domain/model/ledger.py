"""M1 ledger domain models: DownloadJob (processing-state record, §4/§6)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from sat_descarga_masiva.domain.enums.catalog import Direction, ServiceType
from sat_descarga_masiva.domain.model.value_objects import RequestId, Rfc


class JobStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass(frozen=True)
class DownloadJob:
    job_id: str
    client_rfc: Rfc
    service: ServiceType
    direction: Direction
    request_id: RequestId
    query_start: datetime
    query_end: datetime
    policy_version: int
    status: JobStatus
    created_at: datetime
    completed_at: datetime | None = None

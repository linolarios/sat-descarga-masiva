"""Composable query validation (Specification). SAT rules live here, not in adapters."""

from __future__ import annotations

from sat_descarga_masiva.domain.enums.catalog import (
    Direction,
    DocumentStatus,
    RequestType,
    ServiceType,
)
from sat_descarga_masiva.domain.model.query import DownloadQuery


def validate(query: DownloadQuery) -> list[str]:
    errors: list[str] = []
    if query.date_range.start > query.date_range.end:
        errors.append("date_range.start must be <= end")
    # phpcfdi invariant (confirm before prod): received XML must request VIGENTE.
    if (
        query.service is ServiceType.CFDI
        and query.direction is Direction.RECIBIDOS
        and query.request_type is RequestType.CFDI
        and query.document_status is not DocumentStatus.VIGENTE
    ):
        errors.append("recibidos XML requires document_status=VIGENTE")
    return errors

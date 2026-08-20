"""M1: DownloadCursor value + IncrementalDownloadPolicy."""

from datetime import datetime, timedelta

import pytest

from sat_descarga_masiva.application.policies.download import IncrementalDownloadPolicy
from sat_descarga_masiva.domain.enums.catalog import Direction, ServiceType
from sat_descarga_masiva.domain.model.cursor import (
    DownloadCursor,
)
from sat_descarga_masiva.domain.model.value_objects import RequestId, Rfc

RFC = Rfc("AAA010101AAA")


def _cursor() -> DownloadCursor:
    return DownloadCursor(
        client_rfc=RFC,
        service=ServiceType.CFDI,
        direction=Direction.RECIBIDOS,
        query_start=datetime(2026, 1, 1),
        query_end=datetime(2026, 1, 31),
        last_successful_boundary=datetime(2026, 1, 21),
        last_request_id=RequestId("4e80345d-917f-40bb-a98f-4a73939353c5"),
        last_completed_at=datetime(2026, 1, 21, 10),
    )


def test_cursor_holds_full_shape() -> None:
    c = _cursor()
    assert c.client_rfc == RFC
    assert c.service is ServiceType.CFDI
    assert c.direction is Direction.RECIBIDOS
    assert c.last_request_id is not None


def test_cursor_optional_fields_default_none() -> None:
    c = DownloadCursor(
        RFC, ServiceType.CFDI, Direction.RECIBIDOS, datetime(2026, 1, 1), datetime(2026, 1, 31)
    )
    assert c.last_successful_boundary is None
    assert c.last_request_id is None
    assert c.last_completed_at is None


def test_incremental_policy_holds_overlap() -> None:
    p = IncrementalDownloadPolicy(timedelta(hours=6), 2)
    assert p.overlap == timedelta(hours=6)
    assert p.policy_version == 2


def test_incremental_policy_rejects_negative_overlap() -> None:
    with pytest.raises(ValueError):
        IncrementalDownloadPolicy(timedelta(days=-1), 1)
